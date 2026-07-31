package com.banklab.accounts;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
final class NativeMemoryMetrics {
    private static final Logger LOGGER = LoggerFactory.getLogger(NativeMemoryMetrics.class);
    private static final Pattern CATEGORY = Pattern.compile(
        "^\\s*-\\s+(.+?)\\s+\\(reserved=(\\d+)KB, committed=(\\d+)KB\\).*$");
    private static final Pattern TOTAL = Pattern.compile(
        "^Total: reserved=(\\d+)KB, committed=(\\d+)KB.*$");
    private static final long KIBIBYTE = 1024L;

    private final MeterRegistry registry;
    private final Map<String, NativeMemoryValue> values = new ConcurrentHashMap<>();

    NativeMemoryMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    @Scheduled(
        initialDelayString = "${diagnostics.nmt.initial-delay:30s}",
        fixedDelayString = "${diagnostics.nmt.interval:60s}")
    void collect() {
        Process process = null;
        try {
            process = new ProcessBuilder(
                    "jcmd",
                    Long.toString(ProcessHandle.current().pid()),
                    "VM.native_memory",
                    "summary",
                    "scale=KB")
                .redirectErrorStream(true)
                .start();

            if (!process.waitFor(Duration.ofSeconds(10).toMillis(), TimeUnit.MILLISECONDS)) {
                process.destroyForcibly();
                LOGGER.warn("NMT collection timed out");
                return;
            }

            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            if (process.exitValue() != 0) {
                LOGGER.warn("NMT collection failed: {}", output.strip());
                return;
            }
            output.lines().forEach(this::recordLine);
        } catch (IOException exception) {
            LOGGER.warn("Unable to execute jcmd for NMT collection", exception);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            LOGGER.warn("NMT collection was interrupted");
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }

    private void recordLine(String line) {
        Matcher total = TOTAL.matcher(line);
        if (total.matches()) {
            update("Total", total.group(1), total.group(2));
            return;
        }

        Matcher category = CATEGORY.matcher(line);
        if (category.matches()) {
            update(category.group(1).strip(), category.group(2), category.group(3));
        }
    }

    private void update(String category, String reservedKb, String committedKb) {
        NativeMemoryValue value = values.computeIfAbsent(category, this::register);
        value.reserved().set(Long.parseLong(reservedKb) * KIBIBYTE);
        value.committed().set(Long.parseLong(committedKb) * KIBIBYTE);
    }

    private NativeMemoryValue register(String category) {
        AtomicLong reserved = new AtomicLong();
        AtomicLong committed = new AtomicLong();
        Gauge.builder("jvm.native.memory.reserved", reserved, AtomicLong::get)
            .baseUnit("bytes")
            .description("Native memory reserved by JVM category")
            .tag("category", category)
            .register(registry);
        Gauge.builder("jvm.native.memory.committed", committed, AtomicLong::get)
            .baseUnit("bytes")
            .description("Native memory committed by JVM category")
            .tag("category", category)
            .register(registry);
        return new NativeMemoryValue(reserved, committed);
    }

    private record NativeMemoryValue(AtomicLong reserved, AtomicLong committed) {}
}
