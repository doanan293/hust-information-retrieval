package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.en.EnglishAnalyzer;
import org.apache.lucene.analysis.standard.StandardAnalyzer;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Dau vao : 1 thu muc van ban (.txt/.pdf...) + truy van.
 * Dau ra  : chi muc Lucene (thu muc index/) + ket qua tim kiem (console + output/results.txt).
 *
 * Ba kieu chay (--mode):
 *   interactive : nhap lan luot tung truy van tu console, hien ket qua ngay (mac dinh).
 *   batch       : doc tat ca truy van tu file --queries.
 *   segment     : nhap 1 cau tu console, in ket qua TACH TU (VnCoreNLP) - khong dung index.
 *
 * Analyzer (--analyzer): mac dinh "vietnamese" (tach TU bang VnCoreNLP).
 *   Can chay 1 lan:  bash scripts/setup-vncorenlp.sh
 *   Neu chua co VnCoreNLP -> canh bao va tu dong lui ve "standard".
 *
 * Vi du:
 *   java -jar target/lucene-search.jar                              # interactive, analyzer vietnamese
 *   java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt
 *   java -jar target/lucene-search.jar --mode segment                # xem ket qua tach tu
 *   java -jar target/lucene-search.jar --analyzer standard          # khong dung VnCoreNLP
 *   java -jar target/lucene-search.jar --skip-index                 # dung lai chi muc co san
 */
public final class Main {

    public static void main(String[] args) throws Exception {
        forceUtf8Console(); // tieng Viet hien dung tren Windows (console codepage khong phai UTF-8)
        Map<String, String> opt = parseArgs(args);

        Path docsDir   = Path.of(opt.getOrDefault("docs", "data/docs"));
        Path queries   = Path.of(opt.getOrDefault("queries", "data/queries.txt"));
        Path indexDir  = Path.of(opt.getOrDefault("index", "index"));
        Path outFile   = Path.of(opt.getOrDefault("out", "output/results.txt"));
        Path dumpFile  = Path.of(opt.getOrDefault("index-dump", "output/index-dump.txt"));
        int topK       = Integer.parseInt(opt.getOrDefault("topk", "10"));
        String anName  = opt.getOrDefault("analyzer", "vietnamese").toLowerCase(Locale.ROOT);
        Path vnHome    = Path.of(opt.getOrDefault("vn-home", "."));
        String mode    = opt.getOrDefault("mode", "interactive").toLowerCase(Locale.ROOT);
        boolean segment     = mode.startsWith("seg") || opt.containsKey("segment");
        boolean batch       = !segment && (mode.startsWith("b") || opt.containsKey("batch"));
        boolean interactive = !segment && !batch;
        boolean skipIndex   = opt.containsKey("skip-index");

        try (Analyzer analyzer = buildAnalyzer(anName, vnHome)) {
            String effective = analyzer instanceof VietnameseAnalyzer ? "vietnamese"
                    : analyzer instanceof EnglishAnalyzer ? "english" : "standard";

            System.out.println("== Cau hinh ==");
            System.out.println("  mode     : " + (segment ? "segment" : interactive ? "interactive" : "batch"));
            System.out.println("  analyzer : " + effective
                    + (effective.equals("vietnamese") ? " (vn-home: " + vnHome.toAbsolutePath() + ")" : "")
                    + (effective.equals(anName) ? "" : "  [yeu cau: " + anName + "]"));

            if (segment) {
                System.out.println();
                System.out.println("== Tach tu ==");
                SegmentRunner.run(analyzer);
                return;
            }

            System.out.println("  docs     : " + docsDir.toAbsolutePath());
            if (!interactive) {
                System.out.println("  queries  : " + queries.toAbsolutePath());
            }
            System.out.println("  index    : " + indexDir.toAbsolutePath());
            System.out.println("  output   : " + outFile.toAbsolutePath());
            System.out.println("  top-K    : " + topK);
            System.out.println();

            // 1) + 2) Nap van ban va tao chi muc (co the bo qua bang --skip-index)
            if (skipIndex && indexExists(indexDir)) {
                System.out.println("== [1/2] Bo qua lap chi muc, dung lai chi muc co san ==");
                System.out.println("  " + indexDir.toAbsolutePath());
            } else {
                if (skipIndex) {
                    System.out.println("(--skip-index nhung chua co chi muc hop le -> van tao moi)");
                }
                System.out.println("== [1/2] Nap van ban ==");
                List<DocLoader.Doc> docs = DocLoader.load(docsDir);
                if (docs.isEmpty()) {
                    System.err.println("Khong tim thay van ban hop le trong " + docsDir.toAbsolutePath());
                    System.exit(1);
                }
                docs.forEach(d -> System.out.printf("  + %-28s (%,d ky tu)%n", d.id(), d.content().length()));

                System.out.println();
                System.out.println("== [2/2] Tao chi muc ==");
                Files.createDirectories(indexDir);
                int n = Indexer.build(indexDir, docs, analyzer);
                System.out.println("  Da lap chi muc " + n + " van ban -> " + indexDir.toAbsolutePath());
            }

            // Mo ta chi muc vua tao (de xem truc quan)
            IndexDump.writeReport(indexDir, dumpFile);
            System.out.println("  Mo ta chi muc  -> " + dumpFile.toAbsolutePath());

            // 3) Tim kiem
            System.out.println();
            System.out.println("== Tim kiem ==");
            if (interactive) {
                Searcher.runInteractive(indexDir, analyzer, topK, outFile);
            } else {
                List<QueryLoader.Query> qs = QueryLoader.load(queries);
                Searcher.runBatch(indexDir, qs, analyzer, topK, outFile);
            }
        }
    }

    /** Ep System.out/err sang UTF-8 (JDK 17 tren Windows mac dinh dung codepage cua console). */
    private static void forceUtf8Console() {
        try {
            System.setOut(new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8));
            System.setErr(new PrintStream(new FileOutputStream(FileDescriptor.err), true, StandardCharsets.UTF_8));
        } catch (Exception ignored) {
            // giu nguyen stream mac dinh neu khong ep duoc
        }
    }

    private static boolean indexExists(Path indexDir) throws IOException {
        if (!Files.isDirectory(indexDir)) {
            return false;
        }
        try (Directory dir = FSDirectory.open(indexDir)) {
            return DirectoryReader.indexExists(dir);
        }
    }

    private static Analyzer buildAnalyzer(String name, Path vnHome) {
        return switch (name) {
            case "english", "en" -> new EnglishAnalyzer();
            case "standard", "std" -> new StandardAnalyzer();
            case "vietnamese", "vi", "vn" -> vietnameseOrFallback(vnHome);
            default -> {
                System.err.println("Analyzer khong ro '" + name + "', dung StandardAnalyzer.");
                yield new StandardAnalyzer();
            }
        };
    }

    private static Analyzer vietnameseOrFallback(Path vnHome) {
        try {
            return new VietnameseAnalyzer(VnSegmenter.create(vnHome));
        } catch (IOException e) {
            System.err.println();
            System.err.println("!! Chua dung duoc analyzer 'vietnamese' (tach tu VnCoreNLP):");
            System.err.println("   " + e.getMessage().replace("\n", "\n   "));
            System.err.println("   -> Tam thoi dung 'standard' (tach theo am tiet). Chay setup roi chay lai.");
            System.err.println();
            return new StandardAnalyzer();
        }
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> m = new HashMap<>();
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if (a.startsWith("--")) {
                String key = a.substring(2);
                String val = (i + 1 < args.length && !args[i + 1].startsWith("--")) ? args[++i] : "true";
                m.put(key, val);
            }
        }
        return m;
    }
}
