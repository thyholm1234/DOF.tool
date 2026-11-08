package dk.dof.tool;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.os.Build;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import androidx.core.content.ContextCompat;
import com.google.android.gms.location.CurrentLocationRequest;
import com.google.android.gms.location.FusedLocationProviderClient;
import com.google.android.gms.location.LocationCallback;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationResult;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/**
 * Positionstjenesten (forgrundstjeneste af typen "location") – bygget til lavt strømforbrug.
 *
 * Ruteoptagelse og Nær mig-deling stiller meget forskellige krav, så forespørgslen til Googles fused-positionstjeneste
 * afhænger af, hvad der er slået til:
 *
 *  - Rute: GPS-nøjagtighed, et punkt hvert 5. sekund (højst hvert 2.) og kun ved flytning ≥ 5 m. Ligger appen i
 *    baggrunden (skærmen slukket), samles punkterne og leveres højst hvert minut, så processoren kan sove imellem.
 *  - Kun Nær mig: ingen GPS – Wi-Fi/mobilmaster (~100 m, serveren afrunder alligevel til ~100 m), højst hvert 3. minut,
 *    kun ved flytning ≥ 150 m, og leveret samlet højst hvert 10. minut. Står man stille, sker der intet.
 *
 * Der holdes ingen wakelock og ingen vagthund, der genstarter GPS'en: fused-tjenesten vækker selv processen, når der er
 * noget at levere. Positionen til Nær mig sendes direkte herfra til serveren (også når appen er lukket), højst hvert
 * minut og kun ved flytning ≥ 100 m eller hvert 10. minut. Afviser serveren tokenet (deling slået fra et andet sted),
 * holder delingen op med det samme, så telefonen ikke bliver ved med at bruge strøm på det.
 */
public class DofPositionService extends Service {

    private static final String TAG = "DofPosition";
    static final String CHANNEL = "position";
    private static final int NOTIFICATION_ID = 0x0D0F;
    private static final String PREFS = "dof_position";

    // Rute
    private static final long ROUTE_INTERVAL_MS = 5_000L, ROUTE_MIN_INTERVAL_MS = 2_000L, ROUTE_BATCH_HIDDEN_MS = 60_000L;
    private static final float ROUTE_MIN_DISTANCE_M = 5f;
    // Kun Nær mig
    private static final long SHARE_INTERVAL_MS = 5 * 60_000L, SHARE_MIN_INTERVAL_MS = 3 * 60_000L, SHARE_BATCH_MS = 10 * 60_000L;
    private static final float SHARE_MIN_DISTANCE_M = 150f;
    // Afsendelse til serveren
    private static final long POST_MIN_INTERVAL_MS = 60_000L, POST_HEARTBEAT_MS = 10 * 60_000L;
    private static final float POST_MIN_DISTANCE_M = 100f;

    /** Modtager af positionerne i appen (pluginnet), null når ingen lytter. */
    interface Listener {
        void onLocation(Location location);
    }

    static volatile Listener listener;
    static volatile boolean appVisible = true;
    private static volatile DofPositionService instance;

    private FusedLocationProviderClient client;
    private LocationCallback callback;
    private String activeKey;
    private final ExecutorService poster = Executors.newSingleThreadExecutor();

    private boolean route, share;
    private String url, headers, title, message;
    private Location lastPosted;
    private long lastPostedAt;

    // ---------------------------------------------------------------- styring fra pluginnet

    static Intent intent(Context ctx, boolean route, boolean share, String url, JSONObject headers, String title, String message) {
        return new Intent(ctx, DofPositionService.class)
            .putExtra("route", route).putExtra("share", share).putExtra("url", url)
            .putExtra("headers", headers == null ? null : headers.toString()).putExtra("title", title).putExtra("message", message);
    }

    static void stop(Context ctx) {
        ctx.getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply();
        ctx.stopService(new Intent(ctx, DofPositionService.class));
    }

    /** Appen er kommet i forgrunden eller baggrunden: ruten må samles i baggrunden. */
    static void setVisible(boolean visible) {
        appVisible = visible;
        DofPositionService s = instance;
        if (s != null) s.requestUpdates();
    }

    /** Appen er lukket: ingen tager imod rutepunkterne, så GPS'en slukkes (Nær mig-delingen fortsætter). */
    static void dropRoute() {
        DofPositionService s = instance;
        if (s == null || !s.route) return;
        ContextCompat.getMainExecutor(s).execute(() -> {
            s.route = false;
            if (!s.share) s.shutdown();
            else s.requestUpdates();
        });
    }

    static boolean hasLocationPermission(Context ctx) {
        return ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            || ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    // ---------------------------------------------------------------- livscyklus

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        client = LocationServices.getFusedLocationProviderClient(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        boolean restart = intent == null || !intent.hasExtra("route");
        if (restart) {
            // Android genstartede tjenesten (processen var lukket): kun Nær mig-delingen kan fortsætte uden appen
            route = false;
            share = prefs.getBoolean("share", false);
            url = prefs.getString("url", null);
            headers = prefs.getString("headers", null);
            title = prefs.getString("title", "DOF.tool");
            message = prefs.getString("message", "");
        } else {
            route = intent.getBooleanExtra("route", false);
            share = intent.getBooleanExtra("share", false);
            url = intent.getStringExtra("url");
            headers = intent.getStringExtra("headers");
            title = intent.getStringExtra("title");
            message = intent.getStringExtra("message");
            prefs.edit().putBoolean("share", share && url != null).putString("url", url).putString("headers", headers)
                .putString("title", title).putString("message", message).apply();
        }
        boolean shareNative = share && url != null;
        if ((!route && !shareNative && restart) || (!route && !share) || !hasLocationPermission(this) || !promote()) {
            shutdown();
            return START_NOT_STICKY;
        }
        requestUpdates();
        if (share && lastPosted == null) firstFix();
        return shareNative ? START_STICKY : START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        removeUpdates();
        poster.shutdown();
        if (instance == this) instance = null;
        super.onDestroy();
    }

    private void shutdown() {
        removeUpdates();
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private boolean promote() {
        try {
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CHANNEL) == null) {
                NotificationChannel ch = new NotificationChannel(CHANNEL, "Position", NotificationManager.IMPORTANCE_LOW);
                ch.setDescription("Vises, mens DOF.tool optager en rute eller deler din position til Nær mig");
                ch.setShowBadge(false);
                nm.createNotificationChannel(ch);
            }
            Intent open = getPackageManager().getLaunchIntentForPackage(getPackageName());
            PendingIntent tap = open == null ? null
                : PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification n = new NotificationCompat.Builder(this, CHANNEL)
                .setContentTitle(title != null ? title : "DOF.tool")
                .setContentText(message != null ? message : "")
                .setSmallIcon(getApplicationInfo().icon)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setContentIntent(tap)
                .build();
            ServiceCompat.startForeground(this, NOTIFICATION_ID, n,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ? ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION : 0);
            return true;
        } catch (Exception e) {  // fx ingen tilladelse eller start fra baggrunden, som Android ikke tillader
            Log.w(TAG, "Kunne ikke starte som forgrundstjeneste", e);
            return false;
        }
    }

    // ---------------------------------------------------------------- positioner

    private LocationRequest buildRequest() {
        if (route) {
            return new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, ROUTE_INTERVAL_MS)
                .setMinUpdateIntervalMillis(ROUTE_MIN_INTERVAL_MS)
                .setMinUpdateDistanceMeters(ROUTE_MIN_DISTANCE_M)
                .setMaxUpdateDelayMillis(appVisible ? 0 : ROUTE_BATCH_HIDDEN_MS)
                .setWaitForAccurateLocation(false)
                .build();
        }
        return new LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, SHARE_INTERVAL_MS)
            .setMinUpdateIntervalMillis(SHARE_MIN_INTERVAL_MS)
            .setMinUpdateDistanceMeters(SHARE_MIN_DISTANCE_M)
            .setMaxUpdateDelayMillis(SHARE_BATCH_MS)
            .build();
    }

    @SuppressWarnings("MissingPermission")
    private synchronized void requestUpdates() {
        if (!route && !share) return;
        String key = route + ":" + share + ":" + (route && !appVisible);
        if (key.equals(activeKey)) return;
        removeUpdates();
        callback = new LocationCallback() {
            @Override
            public void onLocationResult(LocationResult result) {
                handle(result.getLocations());
            }
        };
        try {
            client.requestLocationUpdates(buildRequest(), callback, Looper.getMainLooper());
            activeKey = key;
        } catch (SecurityException e) {
            Log.w(TAG, "Ingen tilladelse til position", e);
            shutdown();
        }
    }

    private synchronized void removeUpdates() {
        if (callback != null) client.removeLocationUpdates(callback);
        callback = null;
        activeKey = null;
    }

    /** Nær mig flytter sig med det samme, når delingen slås til – ikke først efter første interval. */
    @SuppressWarnings("MissingPermission")
    private void firstFix() {
        try {
            CurrentLocationRequest req = new CurrentLocationRequest.Builder()
                .setPriority(Priority.PRIORITY_BALANCED_POWER_ACCURACY)
                .setMaxUpdateAgeMillis(2 * 60_000L)
                .setDurationMillis(30_000L)
                .build();
            client.getCurrentLocation(req, null).addOnSuccessListener((loc) -> {
                if (loc != null) handle(java.util.Collections.singletonList(loc));
            });
        } catch (SecurityException ignored) {
        }
    }

    private void handle(List<Location> locations) {
        if (locations == null || locations.isEmpty()) return;
        Listener l = listener;
        if (l != null) for (Location loc : locations) l.onLocation(loc);
        if (share && url != null) maybePost(locations.get(locations.size() - 1));
    }

    private void maybePost(Location loc) {
        if (loc.hasAccuracy() && loc.getAccuracy() > 500) return;
        long now = SystemClock.elapsedRealtime();
        if (lastPosted != null) {
            long since = now - lastPostedAt;
            if (since < POST_MIN_INTERVAL_MS) return;
            if (loc.distanceTo(lastPosted) < POST_MIN_DISTANCE_M && since < POST_HEARTBEAT_MS) return;
        }
        lastPosted = loc;
        lastPostedAt = now;
        final String target = url, hdrs = headers;
        final JSONObject body = new JSONObject();
        try {
            body.put("latitude", loc.getLatitude());
            body.put("longitude", loc.getLongitude());
            body.put("accuracy", loc.hasAccuracy() ? loc.getAccuracy() : JSONObject.NULL);
            body.put("time", loc.getTime());
            body.put("source", "native");
        } catch (Exception ignored) {
        }
        poster.execute(() -> post(target, hdrs, body));
    }

    private void post(String target, String hdrs, JSONObject body) {
        HttpURLConnection con = null;
        try {
            con = (HttpURLConnection) new URL(target).openConnection();
            con.setConnectTimeout(15_000);
            con.setReadTimeout(15_000);
            con.setRequestMethod("POST");
            con.setDoOutput(true);
            con.setRequestProperty("Content-Type", "application/json");
            if (hdrs != null) {
                JSONObject h = new JSONObject(hdrs);
                for (Iterator<String> it = h.keys(); it.hasNext(); ) {
                    String k = it.next();
                    con.setRequestProperty(k, h.getString(k));
                }
            }
            try (OutputStream out = con.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int code = con.getResponseCode();
            if (code == 401 || code == 403) {
                // Tokenet gælder ikke længere (deling slået fra, en anden enhed har overtaget): stop delingen
                Log.i(TAG, "Serveren afviste positionstokenet – stopper Nær mig-delingen");
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("share", false).apply();
                ContextCompat.getMainExecutor(this).execute(() -> {
                    share = false;
                    url = null;
                    if (!route) shutdown();
                    else requestUpdates();
                });
            }
        } catch (Exception e) {
            Log.w(TAG, "Positionen kunne ikke sendes: " + e.getMessage());
        } finally {
            if (con != null) con.disconnect();
        }
    }
}
