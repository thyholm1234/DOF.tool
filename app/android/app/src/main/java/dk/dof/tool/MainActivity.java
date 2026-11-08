package dk.dof.tool;

import android.content.Intent;
import android.os.Bundle;
import android.webkit.WebView;
import androidx.activity.OnBackPressedCallback;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DofAsr.class); // tale-indtastning på ture: dansk talemodel (CoRal) på telefonen
        registerPlugin(DofPosition.class); // rute og Nær mig med lavt strømforbrug (erstatter BackgroundGeolocation på Android)
        super.onCreate(savedInstanceState);
        stopLegacyPosition();
        // Tilbageknappen: uden Capacitors App-plugin lukker Android ellers appen. Portalen (window.dofBack i
        // web/js/core.js) lukker først et åbent lag og går ellers tilbage i historikken; er der intet at gå tilbage
        // til, lægges appen i baggrunden i stedet for at blive lukket.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                WebView wv = getBridge().getWebView();
                boolean canGoBack = wv.canGoBack();
                wv.evaluateJavascript("typeof window.dofBack === 'function' && window.dofBack(" + canGoBack + ")", (handled) -> {
                    if ("true".equals(handled)) return;
                    if (wv.canGoBack()) wv.goBack(); // fx fejlsiden, hvor portalen ikke er indlæst
                    else moveTaskToBack(true);
                });
            }
        });
    }

    /** Telefoner opdateret fra en ældre version: det gamle positionsplugin (BackgroundGeolocation – uafbrudt GPS med
     *  wakelock) kan have gemt en konfiguration, som Android genstarter. Den ryddes, så kun DofPosition kører. */
    private void stopLegacyPosition() {
        try {
            getSharedPreferences("CapgoBackgroundGeolocationWatcher", MODE_PRIVATE).edit().clear().apply();
            stopService(new Intent(this, com.capgo.capacitor_background_geolocation.BackgroundGeolocationService.class));
        } catch (Exception ignored) {
        }
    }

    // Portalen får besked, når appen lægges i baggrunden eller kommer frem igen (web/js/lifecycle.js), så den kan lukke
    // livestrømmen og stoppe timere – WebView'ens egen visibilitychange kommer ikke pålideligt på alle telefoner.
    @Override
    public void onStart() {
        super.onStart();
        lifecycle("visible");
    }

    @Override
    public void onStop() {
        lifecycle("hidden");
        super.onStop();
    }

    private void lifecycle(String state) {
        if (getBridge() == null || getBridge().getWebView() == null) return;
        getBridge().getWebView().evaluateJavascript("typeof window.dofLifecycle === 'function' && window.dofLifecycle('" + state + "')", null);
    }
}
