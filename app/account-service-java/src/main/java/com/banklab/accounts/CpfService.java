package com.banklab.accounts;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class CpfService {
    private final byte[] fingerprintKey;

    CpfService(@Value("${banking.identity-secret:}") String masterSecret) {
        if (masterSecret == null || masterSecret.length() < 32) {
            throw new IllegalStateException("BANKING_IDENTITY_SECRET must have at least 32 characters");
        }
        fingerprintKey = hmac(masterSecret.getBytes(StandardCharsets.UTF_8),
            "moura-banking/cpf-fingerprint/v1");
    }

    CpfIdentity identity(String rawCpf) {
        var normalized = normalize(rawCpf);
        if (!valid(normalized)) throw new InvalidCpfException();
        return new CpfIdentity(
            HexFormat.of().formatHex(hmac(fingerprintKey, normalized)),
            normalized.substring(7));
    }

    static String masked(String last4) {
        if (last4 == null) return null;
        if (!last4.matches("\\d{4}")) throw new IllegalStateException("Invalid CPF suffix");
        return "***.***.*%s-%s".formatted(last4.substring(0, 2), last4.substring(2));
    }

    static String normalize(String rawCpf) {
        if (rawCpf == null || !rawCpf.matches("[0-9.\\-\\s]+")) throw new InvalidCpfException();
        return rawCpf.replaceAll("\\D", "");
    }

    static boolean valid(String cpf) {
        if (cpf == null || !cpf.matches("\\d{11}") || cpf.chars().distinct().count() == 1) {
            return false;
        }
        return digit(cpf, 9) == cpf.charAt(9) - '0'
            && digit(cpf, 10) == cpf.charAt(10) - '0';
    }

    private static int digit(String cpf, int length) {
        var sum = 0;
        for (var index = 0; index < length; index++) {
            sum += (cpf.charAt(index) - '0') * (length + 1 - index);
        }
        var value = 11 - (sum % 11);
        return value >= 10 ? 0 : value;
    }

    private static byte[] hmac(byte[] key, String value) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception exception) {
            throw new IllegalStateException("Unable to derive CPF fingerprint", exception);
        }
    }

    record CpfIdentity(String fingerprint, String last4) {}
}
