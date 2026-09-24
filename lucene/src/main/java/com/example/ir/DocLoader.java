package com.example.ir;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/** Quet 1 thu muc, rut van ban tung file -> danh sach document. */
public final class DocLoader {

    /** 1 van ban dau vao. */
    public record Doc(String id, Path path, String content) {}

    private DocLoader() {}

    public static List<Doc> load(Path docsDir) throws IOException {
        if (!Files.isDirectory(docsDir)) {
            throw new IOException("Thu muc van ban khong ton tai: " + docsDir.toAbsolutePath());
        }
        List<Doc> docs = new ArrayList<>();
        try (Stream<Path> walk = Files.walk(docsDir)) {
            List<Path> files = walk
                    .filter(Files::isRegularFile)
                    .filter(TextExtractor::isSupported)
                    .sorted(Comparator.comparing(Path::toString))
                    .toList();
            for (Path file : files) {
                String content;
                try {
                    content = TextExtractor.extract(file);
                } catch (IOException e) {
                    System.err.println("  [bo qua] khong doc duoc " + file + ": " + e.getMessage());
                    continue;
                }
                if (content == null || content.isBlank()) {
                    System.err.println("  [bo qua] rong hoac khong ho tro: " + file);
                    continue;
                }
                String id = docsDir.relativize(file).toString();
                docs.add(new Doc(id, file, content));
            }
        } catch (UncheckedIOException e) {
            throw e.getCause();
        }
        return docs;
    }
}
