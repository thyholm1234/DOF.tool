package dk.dof.tool;

import android.Manifest;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.util.ArrayList;

/**
 * Tale-indtastning på ture (web/js/voice.js): hold-og-tal med genkendelse på telefonen, når den findes.
 *
 * Android 13+ med en dansk model på telefonen bruger SpeechRecognizer.createOnDeviceSpeechRecognizer – ingen net, og
 * slutresultatet kommer kort efter, man slipper. Ellers bruges standardgenkenderen med EXTRA_PREFER_OFFLINE. Mangler den
 * danske model på telefonen, bedes Android om at hente den, og standardgenkenderen bruges i mellemtiden.
 *
 * Events: "partial" {matches}, "final" {matches}, "error" {code, message}.
 */
@CapacitorPlugin(name = "DofSpeech", permissions = { @Permission(strings = { Manifest.permission.RECORD_AUDIO }, alias = "microphone") })
public class DofSpeech extends Plugin {

    private final Handler main = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private boolean onDevice;
    private boolean onDeviceFailed; // dansk mangler på telefonen: brug standardgenkenderen resten af sessionen
    private String language = "da-DK";
    private int maxResults = 5;
    private ArrayList<String> last = new ArrayList<>();

    private boolean onDeviceAvailable() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !onDeviceFailed
            && SpeechRecognizer.isOnDeviceRecognitionAvailable(getContext());
    }

    @PluginMethod
    public void available(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("available", SpeechRecognizer.isRecognitionAvailable(getContext()) || onDeviceAvailable());
        ret.put("onDevice", onDeviceAvailable());
        call.resolve(ret);
    }

    @PluginMethod
    public void requestMicrophone(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) {
            call.resolve(new JSObject().put("granted", true));
        } else {
            requestPermissionForAlias("microphone", call, "micResult");
        }
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
        language = call.getString("language", "da-DK");
        maxResults = call.getInt("maxResults", 5);
        main.post(() -> {
            try {
                begin(onDeviceAvailable());
                JSObject ret = new JSObject();
                ret.put("onDevice", onDevice);
                call.resolve(ret);
            } catch (Exception ex) {
                call.reject(ex.getMessage());
            }
        });
    }

    @PluginMethod
    public void stop(PluginCall call) {
        main.post(() -> {
            if (recognizer != null) recognizer.stopListening(); // slutresultatet kommer som "final"
            call.resolve();
        });
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        main.post(() -> {
            release();
            call.resolve();
        });
    }

    private Intent intent() {
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, language);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, maxResults);
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        i.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        i.putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getContext().getPackageName());
        // Knappen styrer, hvornår der stoppes: pauser i talen må ikke afslutte optagelsen
        i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 15000);
        i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 15000);
        i.putExtra("android.speech.extra.DICTATION_MODE", true);
        return i;
    }

    private void begin(boolean useOnDevice) {
        release();
        onDevice = useOnDevice;
        last = new ArrayList<>();
        recognizer = useOnDevice && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
            ? SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext())
            : SpeechRecognizer.createSpeechRecognizer(getContext());
        recognizer.setRecognitionListener(new Listener());
        recognizer.startListening(intent());
    }

    private void release() {
        if (recognizer != null) {
            recognizer.cancel();
            recognizer.destroy();
            recognizer = null;
        }
    }

    private static ArrayList<String> matches(Bundle b) {
        ArrayList<String> m = b == null ? null : b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return m == null ? new ArrayList<>() : m;
    }

    private class Listener implements RecognitionListener {

        @Override
        public void onPartialResults(Bundle b) {
            ArrayList<String> m = matches(b);
            if (m.isEmpty() || m.equals(last)) return;
            last = m;
            notifyListeners("partial", new JSObject().put("matches", new JSArray(m)));
        }

        @Override
        public void onResults(Bundle b) {
            ArrayList<String> m = matches(b);
            notifyListeners("final", new JSObject().put("matches", new JSArray(m.isEmpty() ? last : m)));
            release();
        }

        @Override
        public void onError(int error) {
            boolean languageMissing = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE);
            if (onDevice && languageMissing) {
                // Dansk findes ikke på telefonen endnu: bed Android om at hente modellen og fortsæt med standardgenkenderen
                onDeviceFailed = true;
                try {
                    SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext()).triggerModelDownload(intent());
                } catch (Exception ignored) {}
                begin(false);
                return;
            }
            JSObject ret = new JSObject();
            ret.put("code", error);
            ret.put("message", text(error));
            ret.put("matches", new JSArray(last)); // det, der nåede at blive hørt
            notifyListeners("error", ret);
            release();
        }

        @Override public void onReadyForSpeech(Bundle params) {}
        @Override public void onBeginningOfSpeech() {}
        @Override public void onRmsChanged(float rmsdB) {}
        @Override public void onBufferReceived(byte[] buffer) {}
        @Override public void onEndOfSpeech() {}
        @Override public void onEvent(int eventType, Bundle params) {}
    }

    private static String text(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH: return "Hørte ingenting";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT: return "Hørte ingenting";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "Ingen forbindelse til talegenkendelsen";
            case SpeechRecognizer.ERROR_AUDIO: return "Mikrofonen kunne ikke bruges";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS: return "Appen må ikke bruge mikrofonen";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY: return "Talegenkendelsen er optaget – prøv igen";
            default: return "Talegenkendelsen fejlede (" + error + ")";
        }
    }
}
