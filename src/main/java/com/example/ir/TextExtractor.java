package com.example.ir;

import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.text.PDFTextStripper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.Locale;
import java.util.Set;

/** Rut van ban tho tu 1 file. Ho tro .txt/.md/.text va .pdf. */
public final class TextExtractor {

    private static final Set<String> PLAIN = Set.of("txt", "md", "text", "csv", "log");

    private TextExtractor() {}

    /**
     * Tra ve noi dung van ban da chuan hoa Unicode NFC, hoac null neu dinh dang
     * khong duoc ho tro. Chuan hoa vi vai file .txt/.pdf co the luu o dang NFD (ky
     * tu goc + dau ket hop rieng, vd mac dinh khi tao file tren macOS hoac mot so
     * PDF); neu de nguyen, ASCIIFoldingFilter (chi xu ly ky tu NFC dung san) se
     * khong bo duoc dau -> truy van co dau khong khop duoc tai lieu do.
     */
    public static String extract(Path file) throws IOException {
        String ext = extension(file);
        if (PLAIN.contains(ext)) {
            return normalize(Files.readString(file, StandardCharsets.UTF_8));
        }
        if ("pdf".equals(ext)) {
            try (PDDocument doc = Loader.loadPDF(file.toFile())) {
                PDFTextStripper stripper = new PDFTextStripper();
                stripper.setSortByPosition(true);
                return normalize(stripper.getText(doc));
            }
        }
        return null;
    }

    private static String normalize(String text) {
        return Normalizer.normalize(text, Normalizer.Form.NFC);
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
