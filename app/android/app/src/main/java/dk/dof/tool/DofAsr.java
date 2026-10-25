package dk.dof.tool;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtSession;
import android.Manifest;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Tale-indtastning på ture med en dansk talemodel på telefonen: CoRal-projektets roest-v3-wav2vec2-315m (int8, ONNX)
 * køres med ONNX Runtime. Ingen lyd forlader telefonen, og der bruges ingen Google-tjenester.
 *
 * Modellen (ca. 355 MB) hentes én gang fra portalens server (/models/, bygget af tools/speech-model) og ligger i appens
 * egne filer. Optagelsen er 16 kHz mono; ved stop køres modellen, og log-sandsynlighederne pr. 20 ms (frames × tegn)
 * sendes til web/js/voice-ctc.js, som afkoder dem mod artslisten, tallene og adfærdslisten.
 *
 * Events: "progress" {done, total} under hentning, "level" {rms} under optagelse.
 */
@CapacitorPlugin(name = "DofAsr", permissions = { @Permission(strings = { Manifest.permission.RECORD_AUDIO }, alias = "microphone") })
public class DofAsr extends Plugin {

    private static final int RATE = 16000;
    private static final int MAX_SECONDS = 30;

    private final ExecutorService worker = Executors.newSingleThreadExecutor(); // model: hentning, indlæsning, kørsel
    private OrtEnvironment env;
    private OrtSession session;
    private String sessionVersion;

    private AudioRecord recorder;
    private Thread reader;
    private volatile boolean recording;
    private short[] audio = new short[RATE * MAX_SECONDS];
    private volatile int samples;

    private File dir() {
        File d = new File(getContext().getFilesDir(), "speech");
        d.mkdirs();
        return d;
    }

    private File model() { return new File(dir(), "model.onnx"); }

    private File meta() { return new File(dir(), "version.txt"); }

    private String installedVersion() {
        try (FileInputStream in = new FileInputStream(meta())) {
            byte[] b = new byte[200];
            int n = in.read(b);
            return n > 0 ? new String(b, 0, n, "UTF-8").trim() : null;
        } catch (Exception e) {
            return null;
        }
    }

    // ---------------------------------------------------------------- model

    @PluginMethod
    public void status(PluginCall call) {
        String v = model().exists() ? installedVersion() : null;
        JSObject ret = new JSObject();
        ret.put("installed", v != null);
        ret.put("version", v);
        ret.put("bytes", v != null ? model().length() : 0);
        ret.put("loaded", session != null);
        ret.put("freeBytes", dir().getUsableSpace());
        call.resolve(ret);
    }

    /** Hent modellen: {url, sha256, size, version}. Kan genoptages (Range), og kontrolleres med SHA-256 før brug. */
    @PluginMethod
    public void install(PluginCall call) {
        String url = call.getString("url"), sha = call.getString("sha256"), version = call.getString("version");
        long size = call.getLong("size", 0L);
        if (url == null || sha == null || version == null) {
            call.reject("url, sha256 og version mangler");
            return;
        }
        worker.execute(() -> {
            File part = new File(dir(), "model.part");
            try {
                if (dir().getUsableSpace() + part.length() < size + 50_000_000L) throw new Exception("Der er ikke plads nok på telefonen");
                long have = part.exists() ? part.length() : 0;
                HttpURLConnection con = (HttpURLConnection) new URL(url).openConnection();
                con.setConnectTimeout(20000);
                con.setReadTimeout(60000);
                if (have > 0) con.setRequestProperty("Range", "bytes=" + have + "-");
                int code = con.getResponseCode();
                if (code == 200) have = 0; // serveren genoptager ikke: forfra
                else if (code != 206) throw new Exception("Serveren svarede " + code);
                long total = size > 0 ? size : have + con.getContentLengthLong();
                try (InputStream in = con.getInputStream(); RandomAccessFile out = new RandomAccessFile(part, "rw")) {
                    out.setLength(have);
                    out.seek(have);
                    byte[] buf = new byte[1 << 16];
                    long done = have, lastNote = 0;
                    for (int n; (n = in.read(buf)) > 0; ) {
                        out.write(buf, 0, n);
                        done += n;
                        if (done - lastNote > 2_000_000L || done == total) {
                            lastNote = done;
                            notifyListeners("progress", new JSObject().put("done", done).put("total", total));
                        }
                    }
                }
                if (!sha256(part).equalsIgnoreCase(sha)) {
                    part.delete();
                    throw new Exception("Modellen blev ikke hentet korrekt – prøv igen");
                }
                closeSession();
                if (!part.renameTo(model())) throw new Exception("Modellen kunne ikke gemmes");
                try (FileOutputStream m = new FileOutputStream(meta())) { m.write(version.getBytes("UTF-8")); }
                call.resolve(new JSObject().put("version", version));
            } catch (Exception ex) {
                call.reject(ex.getMessage() != null ? ex.getMessage() : ex.toString());
            }
        });
    }

    @PluginMethod
    public void remove(PluginCall call) {
        worker.execute(() -> {
            closeSession();
            model().delete();
            meta().delete();
            new File(dir(), "model.part").delete();
            call.resolve();
        });
    }

    /** Indlæs modellen i hukommelsen (tager et par sekunder første gang) – kaldes, når turen åbnes. */
    @PluginMethod
    public void prepare(PluginCall call) {
        worker.execute(() -> {
            try {
                loadModel();
                call.resolve();
            } catch (Exception ex) {
                call.reject(ex.getMessage() != null ? ex.getMessage() : ex.toString());
            }
        });
    }

    private void loadModel() throws Exception {
        String v = installedVersion();
        if (!model().exists() || v == null) throw new Exception("Talemodellen er ikke hentet");
        if (session != null && v.equals(sessionVersion)) return;
        closeSession();
        if (env == null) env = OrtEnvironment.getEnvironment();
        OrtSession.SessionOptions opts = new OrtSession.SessionOptions();
        opts.setIntraOpNumThreads(Math.max(1, Math.min(4, Runtime.getRuntime().availableProcessors() - 1)));
        opts.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        session = env.createSession(model().getAbsolutePath(), opts);
        sessionVersion = v;
    }

    private void closeSession() {
        if (session != null) {
            try { session.close(); } catch (Exception ignored) {}
            session = null;
        }
    }

    private static String sha256(File f) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(f)) {
            byte[] buf = new byte[1 << 16];
            for (int n; (n = in.read(buf)) > 0; ) md.update(buf, 0, n);
        }
        StringBuilder sb = new StringBuilder();
        for (byte b : md.digest()) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    // ---------------------------------------------------------------- mikrofon og optagelse

    @PluginMethod
    public void requestMicrophone(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) call.resolve(new JSObject().put("granted", true));
        else requestPermissionForAlias("microphone", call, "micResult");
    }

    @PermissionCallback
    private void micResult(PluginCall call) {
        call.resolve(new JSObject().put("granted", getPermissionState("microphone") == PermissionState.GRANTED));
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("Appen må ikke bruge mikrofonen");
            return;
        }
        stopRecording();
        try {
            int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            // VOICE_RECOGNITION: ingen automatisk forstærkning/støjfilter, som er lavet til samtaler
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(min, RATE / 5 * 2));
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new Exception("Mikrofonen kunne ikke startes");
            samples = 0;
            recording = true;
            recorder.startRecording();
            reader = new Thread(this::readLoop, "dof-asr-rec");
            reader.start();
            worker.execute(() -> { try { loadModel(); } catch (Exception ignored) {} }); // klar, når der slippes
            call.resolve();
        } catch (Exception ex) {
            stopRecording();
            call.reject(ex.getMessage() != null ? ex.getMessage() : ex.toString());
        }
    }

    private void readLoop() {
        short[] buf = new short[RATE / 10];
        while (recording) {
            int n = recorder.read(buf, 0, buf.length);
            if (n <= 0) continue;
            int room = Math.min(n, audio.length - samples);
            System.arraycopy(buf, 0, audio, samples, room);
            samples += room;
            double sum = 0;
            for (int i = 0; i < n; i++) sum += (double) buf[i] * buf[i];
            notifyListeners("level", new JSObject().put("rms", Math.sqrt(sum / n) / 32768.0));
            if (samples >= audio.length) break; // højst MAX_SECONDS
        }
    }

    private void stopRecording() {
        recording = false;
        if (reader != null) {
            try { reader.join(500); } catch (InterruptedException ignored) {}
            reader = null;
        }
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
            recorder.release();
            recorder = null;
        }
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        stopRecording();
        call.resolve();
    }

    /** Stop optagelsen og kør modellen: {frames, vocab, logprobs (base64, float32 little-endian, log-softmax), ms, seconds}. */
    @PluginMethod
    public void stop(PluginCall call) {
        stopRecording();
        final int n = samples;
        final short[] pcm = new short[n];
        System.arraycopy(audio, 0, pcm, 0, n);
        worker.execute(() -> {
            try {
                if (n < RATE / 4) {
                    call.resolve(new JSObject().put("frames", 0).put("seconds", n / (double) RATE));
                    return;
                }
                loadModel();
                long t0 = System.currentTimeMillis();
                // Wav2Vec2FeatureExtractor(do_normalize=True): middelværdi 0, varians 1
                float[] x = new float[n];
                double mean = 0, var = 0;
                for (int i = 0; i < n; i++) { x[i] = pcm[i] / 32768f; mean += x[i]; }
                mean /= n;
                for (int i = 0; i < n; i++) { double d = x[i] - mean; var += d * d; }
                float scale = (float) (1.0 / Math.sqrt(var / n + 1e-7));
                for (int i = 0; i < n; i++) x[i] = (float) ((x[i] - mean) * scale);
                float[][][] logits;
                try (OnnxTensor input = OnnxTensor.createTensor(env, FloatBuffer.wrap(x), new long[] { 1, n });
                     OrtSession.Result out = session.run(Collections.singletonMap("input_values", input))) {
                    logits = (float[][][]) out.get(0).getValue();
                }
                int frames = logits[0].length, vocab = logits[0][0].length;
                ByteBuffer bb = ByteBuffer.allocate(frames * vocab * 4).order(ByteOrder.LITTLE_ENDIAN);
                for (float[] row : logits[0]) {
                    float max = Float.NEGATIVE_INFINITY;
                    for (float v : row) max = Math.max(max, v);
                    double sum = 0;
                    for (float v : row) sum += Math.exp(v - max);
                    float lse = (float) (max + Math.log(sum));
                    for (float v : row) bb.putFloat(v - lse);
                }
                JSObject ret = new JSObject();
                ret.put("frames", frames);
                ret.put("vocab", vocab);
                ret.put("logprobs", Base64.encodeToString(bb.array(), Base64.NO_WRAP));
                ret.put("ms", System.currentTimeMillis() - t0);
                ret.put("seconds", n / (double) RATE);
                call.resolve(ret);
            } catch (Exception ex) {
                call.reject(ex.getMessage() != null ? ex.getMessage() : ex.toString());
            }
        });
    }
}
