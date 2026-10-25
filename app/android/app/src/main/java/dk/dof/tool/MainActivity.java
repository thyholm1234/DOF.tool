package dk.dof.tool;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DofSpeech.class); // tale-indtastning på ture (genkendelse på telefonen)
        super.onCreate(savedInstanceState);
    }
}
