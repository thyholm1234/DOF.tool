package dk.dof.tool;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Kryptering af små hemmeligheder (positionstokenet i DofPositionService), før de gemmes i SharedPreferences. Nøglen er en
 * AES-256-nøgle i Android Keystore, som ikke kan læses ud af telefonen; værdien gemmes som "v1:" + base64(iv | chiffertekst).
 * Værdier uden præfiks er gemt af en ældre version i klartekst og læses som de er (de krypteres næste gang, de gemmes).
 */
final class DofSecret {
    private static final String TAG = "DofSecret";
    private static final String ALIAS = "dof_position_token";
    private static final String PREFIX = "v1:";
    private static final int IV_LEN = 12, TAG_BITS = 128;

    private DofSecret() {}

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias(ALIAS)) return ((KeyStore.SecretKeyEntry) ks.getEntry(ALIAS, null)).getSecretKey();
        KeyGenerator kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        kg.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return kg.generateKey();
    }

    /** Krypteret værdi til lagring, eller null (også hvis Keystore fejler – så gemmes intet frem for klartekst). */
    static String encrypt(String plain) {
        if (plain == null) return null;
        try {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.ENCRYPT_MODE, key());
            byte[] iv = c.getIV(), ct = c.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            return PREFIX + Base64.encodeToString(ByteBuffer.allocate(iv.length + ct.length).put(iv).put(ct).array(), Base64.NO_WRAP);
        } catch (Exception e) {
            Log.w(TAG, "Kunne ikke kryptere – gemmer ikke værdien", e);
            return null;
        }
    }

    /** Klartekst fra en lagret værdi; null, hvis den ikke kan dekrypteres (fx nøglen er væk efter en gendannelse). */
    static String decrypt(String stored) {
        if (stored == null || !stored.startsWith(PREFIX)) return stored;
        try {
            byte[] all = Base64.decode(stored.substring(PREFIX.length()), Base64.NO_WRAP);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(TAG_BITS, all, 0, IV_LEN));
            return new String(c.doFinal(all, IV_LEN, all.length - IV_LEN), StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.w(TAG, "Kunne ikke dekryptere", e);
            return null;
        }
    }
}
