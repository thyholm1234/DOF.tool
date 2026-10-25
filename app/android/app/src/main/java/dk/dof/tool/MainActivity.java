package dk.dof.tool;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DofAsr.class); // tale-indtastning på ture: dansk talemodel (CoRal) på telefonen
        super.onCreate(savedInstanceState);
    }
}
