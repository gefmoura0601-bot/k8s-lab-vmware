package com.banklab.accounts;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Locale;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class CardCredentialsService {
    private static final String LAB_BIN = "999999";
    private static final BigInteger PAN_RANGE = BigInteger.TEN.pow(9);
    private final byte[] panKey;
    private final byte[] cvvKey;
    private final byte[] panLookupKey;
    private final byte[] paymentRequestKey;
    private final byte[] authorizationKey;

    CardCredentialsService(@Value("${banking.card-secret:}") String masterSecret) {
        if (masterSecret == null || masterSecret.length() < 32) {
            throw new IllegalStateException("BANKING_CARD_SECRET must have at least 32 characters");
        }
        var masterKey = masterSecret.getBytes(StandardCharsets.UTF_8);
        panKey = deriveKey(masterKey, "card-pan");
        cvvKey = deriveKey(masterKey, "card-cvv");
        panLookupKey = deriveKey(masterKey, "card-pan-lookup");
        paymentRequestKey = deriveKey(masterKey, "card-payment-request");
        authorizationKey = deriveKey(masterKey, "card-authorization");
    }

    String pan(UUID cardId) {
        var suffix = new BigInteger(1, hmac(panKey, cardId.toString()))
            .mod(PAN_RANGE).intValue();
        var withoutCheckDigit = LAB_BIN + "%09d".formatted(suffix);
        return withoutCheckDigit + luhnCheckDigit(withoutCheckDigit);
    }

    String cvv(UUID cardId) {
        var value = new BigInteger(1, hmac(cvvKey, cardId.toString()))
            .mod(BigInteger.valueOf(1_000)).intValue();
        return "%03d".formatted(value);
    }

    String panFingerprint(String rawPan) {
        return HexFormat.of().formatHex(hmac(panLookupKey, normalizePan(rawPan)));
    }

    String paymentFingerprint(CardService.CardPaymentRequest request) {
        var card = request.card();
        var canonical = new StringBuilder();
        append(canonical, request.paymentId());
        append(canonical, request.merchantId());
        append(canonical, request.merchantName());
        append(canonical, request.orderReference());
        append(canonical, request.amount() == null ? null : request.amount().toPlainString());
        append(canonical, request.normalizedCurrency());
        append(canonical, card == null ? null : normalizePan(card.number()));
        append(canonical, card == null ? null : normalizeText(card.holderName()));
        append(canonical, card == null ? null : card.expiryMonth());
        append(canonical, card == null ? null : card.expiryYear());
        append(canonical, card == null ? null : card.cvv());
        append(canonical, request.paymentType());
        append(canonical, request.installments());
        return HexFormat.of().formatHex(hmac(paymentRequestKey, canonical.toString()));
    }

    String authorizationCode(UUID paymentId) {
        var value = new BigInteger(1, hmac(authorizationKey, paymentId.toString()))
            .mod(BigInteger.valueOf(1_000_000)).intValue();
        return "%06d".formatted(value);
    }

    boolean validPan(String rawPan) {
        var normalized = normalizePan(rawPan);
        if (!normalized.matches("\\d{16}") || !normalized.startsWith(LAB_BIN)) return false;
        var sum = 0;
        var doubleDigit = false;
        for (var index = normalized.length() - 1; index >= 0; index--) {
            var digit = normalized.charAt(index) - '0';
            if (doubleDigit) {
                digit *= 2;
                if (digit > 9) digit -= 9;
            }
            sum += digit;
            doubleDigit = !doubleDigit;
        }
        return sum % 10 == 0;
    }

    boolean validCvv(UUID cardId, String suppliedCvv) {
        if (suppliedCvv == null) return false;
        return MessageDigest.isEqual(
            cvv(cardId).getBytes(StandardCharsets.UTF_8),
            suppliedCvv.trim().getBytes(StandardCharsets.UTF_8));
    }

    boolean sameFingerprint(String expected, String actual) {
        return expected != null && actual != null && MessageDigest.isEqual(
            expected.getBytes(StandardCharsets.UTF_8), actual.getBytes(StandardCharsets.UTF_8));
    }

    String normalizePan(String rawPan) {
        return rawPan == null ? "" : rawPan.replace(" ", "").replace("-", "");
    }

    String lastFour(String rawPan) {
        var normalized = normalizePan(rawPan);
        return normalized.length() < 4 ? "****" : normalized.substring(normalized.length() - 4);
    }

    static String normalizeText(String value) {
        return value == null ? "" : value.trim().replaceAll("\\s+", " ").toUpperCase(Locale.ROOT);
    }

    private static void append(StringBuilder target, Object value) {
        var text = value == null ? "" : value.toString();
        target.append(text.length()).append(':').append(text).append('|');
    }

    private static byte[] deriveKey(byte[] masterKey, String domain) {
        return hmac(masterKey, "moura-banking/" + domain + "/v1");
    }

    private static byte[] hmac(byte[] key, String value) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception exception) {
            throw new IllegalStateException("Unable to derive card credentials", exception);
        }
    }

    private static int luhnCheckDigit(String value) {
        var sum = 0;
        var doubleDigit = true;
        for (var index = value.length() - 1; index >= 0; index--) {
            var digit = value.charAt(index) - '0';
            if (doubleDigit) {
                digit *= 2;
                if (digit > 9) digit -= 9;
            }
            sum += digit;
            doubleDigit = !doubleDigit;
        }
        return (10 - (sum % 10)) % 10;
    }
}
