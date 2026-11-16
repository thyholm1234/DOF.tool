package dk.dof.tool;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;
import androidx.core.content.ContextCompat;

/**
 * Efter telefonens opstart genstartes Nær mig-delingen, hvis den var slået til og «Tillad altid» er givet. Tjenesten startes uden
 * extras, så dens genstartsgren læser url og token fra SharedPreferences. En fejl her må ikke vælte opstarten.
 */
public class DofBootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        try {
            boolean share = ctx.getSharedPreferences("dof_position", Context.MODE_PRIVATE).getBoolean("share", false);
            if (!share || !DofPositionService.hasBackgroundPermission(ctx)) return;
            ContextCompat.startForegroundService(ctx, new Intent(ctx, DofPositionService.class));
        } catch (Exception e) {
            Log.w("DofPosition", "Nær mig kunne ikke genstartes efter opstart", e);
        }
    }
}
