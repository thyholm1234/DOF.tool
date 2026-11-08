package dk.dof.tool;

import android.Manifest;
import android.content.Context;
import android.location.Location;
import android.location.LocationManager;
import android.os.Build;
import androidx.core.content.ContextCompat;
import androidx.core.location.LocationManagerCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import org.json.JSONObject;

/**
 * Position til ruteoptagelse og Nær mig med lavt strømforbrug (se DofPositionService). Erstatter på Android pluginnet
 * BackgroundGeolocation, som kørte rå GPS uafbrudt med en wakelock – også når kun Nær mig skulle bruge positionen.
 *
 * start({route, share, url?, headers?, title, message}) er et vedvarende kald: hver position leveres som
 * {latitude, longitude, accuracy, time}. Fejl afvises med koden NOT_AUTHORIZED (tilladelse/positionstjenester slået fra)
 * eller START_FAILED. stop() stopper tjenesten.
 */
@CapacitorPlugin(name = "DofPosition", permissions = {
    @Permission(strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION }, alias = "location"),
    @Permission(strings = { "android.permission.POST_NOTIFICATIONS" }, alias = "notifications")
})
public class DofPosition extends Plugin {

    private PluginCall watch;

    @PluginMethod(returnType = PluginMethod.RETURN_CALLBACK)
    public void start(PluginCall call) {
        call.setKeepAlive(true);
        if (getPermissionState("location") != PermissionState.GRANTED) {
            if (!call.getBoolean("requestPermissions", true)) {
                call.reject("Appen må ikke bruge din position", "NOT_AUTHORIZED");
                return;
            }
            requestPermissionForAlias("location", call, "locationResult");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void locationResult(PluginCall call) {
        if (getPermissionState("location") != PermissionState.GRANTED) {
            call.reject("Appen må ikke bruge din position", "NOT_AUTHORIZED");
            return;
        }
        // Forgrundstjenestens notifikation (Android 13+) – uden den kører tjenesten stadig, blot uden synlig besked
        if (Build.VERSION.SDK_INT >= 33 && getPermissionState("notifications") != PermissionState.GRANTED) {
            requestPermissionForAlias("notifications", call, "notificationResult");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void notificationResult(PluginCall call) {
        begin(call);
    }

    private void begin(PluginCall call) {
        Context ctx = getContext();
        LocationManager lm = (LocationManager) ctx.getSystemService(Context.LOCATION_SERVICE);
        if (lm == null || !LocationManagerCompat.isLocationEnabled(lm)) {
            call.reject("Positionstjenester er slået fra på telefonen", "NOT_AUTHORIZED");
            return;
        }
        if (watch != null && watch != call) watch.release(bridge);
        watch = call;
        DofPositionService.listener = this::deliver;
        DofPositionService.appVisible = true;
        JSONObject headers = call.getObject("headers", null);
        try {
            ContextCompat.startForegroundService(ctx, DofPositionService.intent(ctx, call.getBoolean("route", false),
                call.getBoolean("share", false), call.getString("url", null), headers,
                call.getString("title", "DOF.tool bruger din position"), call.getString("message", "")));
            call.resolve(new JSObject().put("started", true));
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Positionstjenesten kunne ikke startes", "START_FAILED");
        }
    }

    private void deliver(Location loc) {
        PluginCall c = watch;
        if (c == null) return;
        JSObject o = new JSObject();
        o.put("latitude", loc.getLatitude());
        o.put("longitude", loc.getLongitude());
        o.put("accuracy", loc.hasAccuracy() ? loc.getAccuracy() : null);
        o.put("time", loc.getTime());
        c.resolve(o);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        DofPositionService.listener = null;
        if (watch != null) {
            watch.release(bridge);
            watch = null;
        }
        DofPositionService.stop(getContext());
        call.resolve();
    }

    @Override
    protected void handleOnResume() {
        DofPositionService.setVisible(true);
    }

    @Override
    protected void handleOnPause() {
        DofPositionService.setVisible(false);
    }

    @Override
    protected void handleOnDestroy() {
        // Aktiviteten lukkes: positionerne har ingen modtager i appen længere (Nær mig sendes stadig direkte herfra)
        DofPositionService.listener = null;
        watch = null;
        DofPositionService.dropRoute();
    }
}
