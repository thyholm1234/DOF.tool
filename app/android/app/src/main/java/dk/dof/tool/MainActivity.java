package dk.dof.tool;

import android.os.Bundle;
import android.webkit.WebView;
import androidx.activity.OnBackPressedCallback;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DofAsr.class); // tale-indtastning på ture: dansk talemodel (CoRal) på telefonen
        super.onCreate(savedInstanceState);
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
}
