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
 *
 * backgroundStatus() / requestBackground() svarer {state: "granted" | "denied" | "prompt" | "unsupported"} for «Tillad altid»
 * (ACCESS_BACKGROUND_LOCATION), som gør, at Nær mig kan genopstå af sig selv, når Android har lukket appen eller telefonen er
 * genstartet. start() beder aldrig om den, og ruteoptagelse kræver den ikke.
 */
@CapacitorPlugin(name = "DofPosition", permissions = {
    @Permission(strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION }, alias = "location"),
    @Permission(strings = { "android.permission.POST_NOTIFICATIONS" }, alias = "notifications"),
    // «Tillad altid» (valgfri, kun til Nær mig): findes først fra Android 10 – før da gælder almindelig position også i baggrunden
    @Permission(strings = { "android.permission.ACCESS_BACKGROUND_LOCATION" }, alias = "backgroundLocation")
})
public class DofPosition extends Plugin {

    private PluginCall watch;

    @Override
    public void load() {
        // Pluginnet oprettes sammen med aktiviteten: en ny aktivitet har en ny WebView uden optagelse, så en rute i tjenesten er forældreløs
        DofPositionService.dropRoute();
    }

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

    private JSObject backgroundState() {
        String state = "unsupported";  // under Android 10 findes tilladelsen ikke (tjenesten behandler det som givet)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            PermissionState s = getPermissionState("backgroundLocation");
            state = s == PermissionState.GRANTED ? "granted" : s == PermissionState.DENIED ? "denied" : "prompt";
        }
        return new JSObject().put("state", state);
    }

    @PluginMethod
    public void backgroundStatus(PluginCall call) {
        call.resolve(backgroundState());
    }

    @PluginMethod
    public void requestBackground(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q || getPermissionState("backgroundLocation") == PermissionState.GRANTED) {
            call.resolve(backgroundState());
            return;
        }
        // Skal bedes om alene og først, når almindelig position er givet – ellers afviser Android uden dialog
        if (getPermissionState("location") != PermissionState.GRANTED) {
            call.reject("Appen må ikke bruge din position", "NOT_AUTHORIZED");
            return;
        }
        // Fra Android 11 sender systemet selv brugeren til indstillingssiden, hvor «Tillad altid» vælges
        requestPermissionForAlias("backgroundLocation", call, "backgroundResult");
    }

    @PermissionCallback
    private void backgroundResult(PluginCall call) {
        call.resolve(backgroundState());
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

    /** Siden er indlæst forfra uden optagelse: en rute i tjenesten er forældreløs og droppes (Nær mig fortsætter). */
    @PluginMethod
    public void dropRoute(PluginCall call) {
        DofPositionService.dropRoute();
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
