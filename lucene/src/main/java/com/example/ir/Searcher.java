package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexReader;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Term;
import org.apache.lucene.queryparser.classic.ParseException;
import org.apache.lucene.queryparser.classic.QueryParser;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.BoostQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.MatchNoDocsQuery;
import org.apache.lucene.search.PhraseQuery;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.search.highlight.Highlighter;
import org.apache.lucene.search.highlight.InvalidTokenOffsetsException;
import org.apache.lucene.search.highlight.QueryScorer;
import org.apache.lucene.search.highlight.SimpleHTMLFormatter;
import org.apache.lucene.search.highlight.SimpleSpanFragmenter;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;

/** Chay truy van tren chi muc theo 2 kieu: batch (tu file) va interactive (nhap tay). */
public final class Searcher {

    static final String DEFAULT_FIELD = "content";

    // Cac dau hieu cu phap QueryParser: co bat ky cai nao -> KHONG tu dong coi la cum.
    private static final java.util.regex.Pattern LUCENE_SYNTAX = java.util.regex.Pattern.compile(
            "[\"()\\[\\]{}:^~*?\\\\]|\\bAND\\b|\\bOR\\b|\\bNOT\\b");

    private static final int FRAGMENT_CHARS = 100;   // do rong doan trich quanh tu khoa
    private static final int MAX_FRAGMENTS = 3;      // so doan trich toi da moi ket qua
    private static final int FALLBACK_CHARS = 200;   // khi khong lam noi bat duoc

    // Highlighter boc tu khop bang 2 ky tu danh dau noi bo, sau do teeSink dich ra:
    //  - console (neu la terminal that): to nen vang, chu den (ma ANSI)
    //  - file .txt (va console khi bi chuyen huong): boc bang « ... »
    private static final String MARK_A = "";
    private static final String MARK_B = "";
    private static final String ANSI_ON = "[30;43m";
    private static final String ANSI_OFF = "[0m";
    private static final String TEXT_ON = "«";
    private static final String TEXT_OFF = "»";
    private static final boolean COLOR = colorEnabled();

    /** Console co ho tro ANSI khong? Tren Windows chi bat neu la Windows Terminal / ConEmu / VS Code. */
    private static boolean colorEnabled() {
        if (System.getenv("NO_COLOR") != null) {
            return false;
        }
        if (System.console() == null) {
            return false; // bi pipe/redirect
        }
        String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        if (os.contains("win")) {
            return System.getenv("WT_SESSION") != null       // Windows Terminal
                    || System.getenv("ConEmuANSI") != null    // ConEmu / Cmder
                    || "1".equals(System.getenv("ANSICON"))
                    || System.getenv("TERM_PROGRAM") != null;  // VS Code terminal
        }
        return true;
    }

    private Searcher() {}

    /** Kieu 1: doc toan bo truy van tu danh sach (nap tu file), in console + ghi file. */
    public static void runBatch(Path indexDir, List<QueryLoader.Query> queries, Analyzer analyzer,
                                int topK, Path outFile) throws IOException {

        Files.createDirectories(outFile.toAbsolutePath().getParent());

        try (Directory dir = FSDirectory.open(indexDir);
             DirectoryReader reader = DirectoryReader.open(dir);
             BufferedWriter out = Files.newBufferedWriter(outFile, StandardCharsets.UTF_8)) {

            IndexSearcher searcher = new IndexSearcher(reader);
            StoredFields storedFields = searcher.storedFields();
            QueryParser parser = newParser(analyzer);
            Consumer<String> sink = teeSink(out);

            sink.accept("# Ket qua tim kiem Lucene (che do batch)");
            sink.accept("# So van ban trong chi muc: " + reader.numDocs());
            sink.accept("# So truy van: " + queries.size() + " | top-K = " + topK);
            sink.accept("");

            for (QueryLoader.Query q : queries) {
                executeAndFormat(searcher, reader, storedFields, analyzer, parser, q, topK, sink);
            }
        }
        System.out.println();
        System.out.println("Da ghi ket qua chi tiet vao: " + outFile.toAbsolutePath());
    }

    /** Kieu 2: nhap lan luot tung truy van tu console, hien ket qua ngay sau moi truy van. */
    public static void runInteractive(Path indexDir, Analyzer analyzer,
                                      int topK, Path outFile) throws IOException {

        Files.createDirectories(outFile.toAbsolutePath().getParent());

        try (Directory dir = FSDirectory.open(indexDir);
             DirectoryReader reader = DirectoryReader.open(dir);
             BufferedWriter out = Files.newBufferedWriter(outFile, StandardCharsets.UTF_8);
             BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {

            IndexSearcher searcher = new IndexSearcher(reader);
            StoredFields storedFields = searcher.storedFields();
            QueryParser parser = newParser(analyzer);
            Consumer<String> sink = teeSink(out);

            sink.accept("# Ket qua tim kiem Lucene (che do interactive)");
            sink.accept("# So van ban trong chi muc: " + reader.numDocs() + " | top-K = " + topK);
            sink.accept("");
            System.out.println("Nhap truy van roi Enter. Go ':q' (hoac Ctrl+D) de thoat.");
            if (analyzer instanceof VietnameseAnalyzer) {
                System.out.println("(cum tieng Viet cu go binh thuong, khong can ngoac kep)");
            }

            int n = 0;
            while (true) {
                System.out.print("\ntruy van> ");
                System.out.flush();
                String line = in.readLine();
                if (line == null) break;                       // Ctrl+D / het input
                line = line.strip();
                if (line.isEmpty()) continue;
                if (line.equals(":q") || line.equals(":quit") || line.equals("exit")) break;

                QueryLoader.Query q = new QueryLoader.Query("Q" + (++n), line);
                executeAndFormat(searcher, reader, storedFields, analyzer, parser, q, topK, sink);
            }
            sink.accept("");
            sink.accept("# Ket thuc phien: " + n + " truy van.");
        }
        System.out.println("\nDa ghi lai phien vao: " + outFile.toAbsolutePath());
    }

    // ---- phan dung chung ----

    private static QueryParser newParser(Analyzer analyzer) {
        QueryParser parser = new QueryParser(DEFAULT_FIELD, analyzer);
        parser.setDefaultOperator(QueryParser.Operator.OR);
        return parser;
    }

    /** Do "xe dich" cho phep giua cac am tiet trong PhraseQuery (chiu duoc 1 dao vi tri). */
    private static final int VN_SLOP = 2;

    /**
     * Truy van cho 1 cum tieng Viet "tron":
     *   PhraseQuery tren CHUOI AM TIET (slop {@value VN_SLOP})^2   SHOULD  - clause chinh, chinh xac
     *   + moi TU GHEP la 1 TermQuery ^0.4                          SHOULD  - phu, giup xep hang
     *
     * Vi am tiet phai nam GAN NHAU dung thu tu nen "dai hoc" chi khop noi co "dai"
     * ngay truoc "hoc", KHONG khop "sinh hoc" / "khoa hoc". Neu tap van ban that su
     * khong co cum do -> 0 ket qua (dung), khong co clause AND keo dai lung tung.
     * Nhieu khai niem roi thi go co toan tu: Lucene AND "tieng Viet".
     */
    private static Query buildVietnamesePhraseQuery(Analyzer analyzer, String text) throws IOException {
        List<String> words = analyzeToTokens(analyzer, text);
        if (words.isEmpty()) {
            return new MatchNoDocsQuery("khong con tu khoa");
        }

        List<String> sylls = new ArrayList<>();
        for (String w : words) {
            if (w.indexOf('_') > 0) {
                for (String s : w.split("_")) {
                    if (!s.isBlank()) {
                        sylls.add(s);
                    }
                }
            } else {
                sylls.add(w);
            }
        }
        if (sylls.size() == 1) {
            return new TermQuery(new Term(DEFAULT_FIELD, sylls.get(0)));
        }

        PhraseQuery.Builder pb = new PhraseQuery.Builder();
        pb.setSlop(VN_SLOP);
        for (String s : sylls) {
            pb.add(new Term(DEFAULT_FIELD, s));
        }

        BooleanQuery.Builder bb = new BooleanQuery.Builder();
        bb.add(new BoostQuery(pb.build(), 2.0f), BooleanClause.Occur.SHOULD);
        for (String w : words) {
            if (w.indexOf('_') > 0) { // chi tu ghep (du dac trung) moi lam clause phu
                bb.add(new BoostQuery(new TermQuery(new Term(DEFAULT_FIELD, w)), 0.4f),
                        BooleanClause.Occur.SHOULD);
            }
        }
        return bb.build();
    }

    /** Tach text bang analyzer, chi lay TU (bo qua am tiet type=SYLLABLE va dau cau). */
    private static List<String> analyzeToTokens(Analyzer analyzer, String text) throws IOException {
        List<String> out = new ArrayList<>();
        try (TokenStream ts = analyzer.tokenStream(DEFAULT_FIELD, text)) {
            CharTermAttribute term = ts.addAttribute(CharTermAttribute.class);
            org.apache.lucene.analysis.tokenattributes.TypeAttribute type =
                    ts.addAttribute(org.apache.lucene.analysis.tokenattributes.TypeAttribute.class);
            ts.reset();
            while (ts.incrementToken()) {
                if (CompoundSplitFilter.SYLLABLE_TYPE.equals(type.type())) {
                    continue; // am tiet tach ra tu tu ghep
                }
                String tok = term.toString();
                if (tok.chars().anyMatch(Character::isLetterOrDigit)) {
                    out.add(tok);
                }
            }
            ts.end();
        }
        return out;
    }

    /** Consumer ghi dong thoi ra console (co the co mau) va ra file (« ... »). */
    private static Consumer<String> teeSink(BufferedWriter out) {
        return line -> {
            String forConsole = COLOR
                    ? line.replace(MARK_A, ANSI_ON).replace(MARK_B, ANSI_OFF)
                    : line.replace(MARK_A, TEXT_ON).replace(MARK_B, TEXT_OFF);
            System.out.println(forConsole);
            try {
                out.write(line.replace(MARK_A, TEXT_ON).replace(MARK_B, TEXT_OFF));
                out.newLine();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        };
    }

    /** Parse + tim kiem 1 truy van, xuat ket qua kem doan trich chua tu khoa. */
    private static void executeAndFormat(IndexSearcher searcher, IndexReader reader, StoredFields storedFields,
                                         Analyzer analyzer, QueryParser parser, QueryLoader.Query q,
                                         int topK, Consumer<String> sink) throws IOException {
        sink.accept("==================================================");
        sink.accept("Truy van " + q.id() + ": " + q.text());

        // Voi analyzer tieng Viet: truy van "tron" (nhieu tu, khong co toan tu) duoc dung
        // thanh (cum dung thu tu)^2  HOAC  tung tu rieng le. Nho clause "tung tu" nen van
        // ra ket qua khi bo tach tu chia cum hoi khac giua truy van va tai lieu.
        // Muon dieu khien tay thi dung AND/OR/NOT/ngoac kep nhu binh thuong.
        String raw = q.text().strip();
        boolean autoPhrase = analyzer instanceof VietnameseAnalyzer
                && raw.contains(" ")
                && !LUCENE_SYNTAX.matcher(raw).find();

        Query luceneQuery;
        if (autoPhrase) {
            luceneQuery = buildVietnamesePhraseQuery(analyzer, raw);
            sink.accept("  (tu hieu la cum tieng Viet; them AND/OR/\"\" de tuy chinh)");
        } else {
            try {
                luceneQuery = parser.parse(q.text());
            } catch (ParseException e) {
                try {
                    luceneQuery = parser.parse(QueryParser.escape(q.text()));
                } catch (ParseException e2) {
                    sink.accept("  [loi] khong phan tich duoc truy van: " + e2.getMessage());
                    sink.accept("");
                    return;
                }
            }
        }

        String parsed = luceneQuery.toString();
        if (parsed.isBlank() || luceneQuery instanceof MatchNoDocsQuery) {
            sink.accept("  (khong con tu khoa nao sau khi loc tu dung / dau cau)");
            sink.accept("");
            return;
        }

        TopDocs hits = searcher.search(luceneQuery, topK);
        long total = hits.totalHits.value;
        sink.accept("  Query da parse: " + parsed);
        sink.accept("  Tong so tai lieu khop: " + total
                + (total > topK ? "  (hien thi " + topK + " ket qua dau)" : ""));

        if (hits.scoreDocs.length == 0) {
            sink.accept("  (khong co ket qua)");
            sink.accept("");
            return;
        }

        Highlighter highlighter = buildHighlighter(luceneQuery, reader);
        int rank = 0;
        for (ScoreDoc sd : hits.scoreDocs) {
            Document d = storedFields.document(sd.doc);
            rank++;
            sink.accept(String.format("  %2d. score=%.4f  id=%s", rank, sd.score, d.get("id")));
            sink.accept("      file: " + d.get("path"));
            sink.accept("      " + kwic(highlighter, analyzer, d.get("content")));
        }
        sink.accept("");
    }

    private static Highlighter buildHighlighter(Query query, IndexReader reader) throws IOException {
        // QueryScorer(query, reader, field): rewrite duoc ca prefix*/wildcard truoc khi lam noi bat.
        QueryScorer scorer = new QueryScorer(query, reader, DEFAULT_FIELD);
        Highlighter h = new Highlighter(new SimpleHTMLFormatter(MARK_A, MARK_B), scorer);
        h.setTextFragmenter(new SimpleSpanFragmenter(scorer, FRAGMENT_CHARS));
        return h;
    }

    /** Cat doan van ban quanh tu khoa (keyword-in-context); tu khop duoc danh dau de teeSink to sang. */
    private static String kwic(Highlighter highlighter, Analyzer analyzer, String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        try (TokenStream ts = analyzer.tokenStream(DEFAULT_FIELD, text)) {
            String frag = highlighter.getBestFragments(ts, text, MAX_FRAGMENTS, " … ");
            if (frag != null && !frag.isBlank()) {
                String cleaned = frag.replaceAll("\\s+", " ")
                        .replace(MARK_A + " " + MARK_B, "")  // bo danh dau rong (offset xap xi)
                        .replace(MARK_A + MARK_B, "")
                        .strip();
                if (!cleaned.isBlank()) {
                    return "… " + cleaned + " …";
                }
            }
        } catch (IOException | InvalidTokenOffsetsException e) {
            // roi xuong doan mac dinh
        }
        String flat = text.replaceAll("\\s+", " ").strip();
        return flat.length() <= FALLBACK_CHARS ? flat : flat.substring(0, FALLBACK_CHARS) + "...";
    }
}
