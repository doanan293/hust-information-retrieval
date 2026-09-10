package com.example.ir;

import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.text.PDFTextStripper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Set;

/** Rut van ban tho tu 1 file. Ho tro .txt/.md/.text va .pdf. */
public final class TextExtractor {

    private static final Set<String> PLAIN = Set.of("txt", "md", "text", "csv", "log");

    private TextExtractor() {}

    /** Tra ve noi dung van ban, hoac null neu dinh dang khong duoc ho tro. */
    public static String extract(Path file) throws IOException {
        String ext = extension(file);
        if (PLAIN.contains(ext)) {
            return Files.readString(file, StandardCharsets.UTF_8);
        }
        if ("pdf".equals(ext)) {
            try (PDDocument doc = Loader.loadPDF(file.toFile())) {
                PDFTextStripper stripper = new PDFTextStripper();
                stripper.setSortByPosition(true);
                return stripper.getText(doc);
            }
        }
        return null;
    }

    public static boolean isSupported(Path file) {
        String ext = extension(file);
        return PLAIN.contains(ext) || "pdf".equals(ext);
    }

    private static String extension(Path file) {
        String name = file.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1).toLowerCase(Locale.ROOT);
    }
}
