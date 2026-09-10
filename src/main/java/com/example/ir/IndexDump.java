package com.example.ir;

import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.FieldInfo;
import org.apache.lucene.index.FieldInfos;
import org.apache.lucene.index.IndexOptions;
import org.apache.lucene.index.MultiTerms;
import org.apache.lucene.index.PostingsEnum;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Terms;
import org.apache.lucene.index.TermsEnum;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;
import org.apache.lucene.util.BytesRef;

import java.io.BufferedWriter;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

/**
 * In / ghi noi dung chi muc Lucene cho de nhin (file .cfs la nhi phan).
 *
 * Tu dong: Main goi {@link #writeReport} sau khi lap chi muc -> output/index-dump.txt
 *
 * Thu cong:
 *   java -cp target/lucene-search.jar com.example.ir.IndexDump [indexDir] [tuy chon]
 *     --report <file>          ghi mo ta day du ra file
 *     --field <ten>            chi in term cua 1 field
 *     --limit <N>              toi da N term moi field (mac dinh 50; 0 = tat ca)
 *     --postings <field:term>  in danh sach doc + vi tri chua term do
 *     --docs                   in ca stored fields cua tung tai lieu
 */
public final class IndexDump {

    private static final int REPORT_TERM_CAP = 3000;   // gioi han term/field khi ghi file
    private static final int PREVIEW = 200;            // do dai content in kem
    private static final int DOC_LIST_CAP = 15;        // so ten tai lieu toi da in cho 1 term

    private IndexDump() {}

    // ---------------------------------------------------------------- CLI

    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8));

        Path indexDir = Path.of("index");
        String onlyField = null;
        int limit = 50;
        String postings = null;
        String report = null;
        boolean showDocs = false;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--field"    -> onlyField = args[++i];
                case "--limit"    -> limit = Integer.parseInt(args[++i]);
                case "--postings" -> postings = args[++i];
                case "--report"   -> report = args[++i];
                case "--docs"     -> showDocs = true;
                default -> { if (!args[i].startsWith("--")) indexDir = Path.of(args[i]); }
            }
        }

        if (report != null) {
            writeReport(indexDir, Path.of(report));
            System.out.println("Da ghi mo ta chi muc -> " + Path.of(report).toAbsolutePath());
            return;
        }

        try (Directory dir = FSDirectory.open(indexDir);
             DirectoryReader reader = DirectoryReader.open(dir)) {

            printOverview(reader, indexDir, System.out::println);
            FieldInfos fis = FieldInfos.getMergedFieldInfos(reader);
            printFields(reader, fis, System.out::println);

            if (postings != null) {
                dumpPostings(reader, postings);
                return;
            }

            String[] docId = buildDocIds(reader);
            System.out.println();
            System.out.println("== Tu dien term (chi muc nguoc)  |  term -> danh sach tai lieu chua no ==");
            for (FieldInfo fi : fis) {
                if (fi.getIndexOptions() == IndexOptions.NONE) continue;
                if (onlyField != null && !onlyField.equals(fi.name)) continue;
                dumpTerms(reader, docId, fi.name, limit, System.out::println);
            }

            if (showDocs) {
                System.out.println();
                System.out.println("== Truong da luu (stored fields) theo tai lieu ==");
                dumpDocs(reader, System.out::println);
            }
        }
    }

    // ---------------------------------------------------------------- Bao cao ghi file

    /** Ghi mo ta day du cua chi muc ra 1 file van ban (UTF-8). */
    public static void writeReport(Path indexDir, Path outFile) throws IOException {
        Path parent = outFile.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        try (Directory dir = FSDirectory.open(indexDir);
             DirectoryReader reader = DirectoryReader.open(dir);
             BufferedWriter w = Files.newBufferedWriter(outFile, StandardCharsets.UTF_8)) {

            Line out = s -> {
                try {
                    w.write(s);
                    w.write('\n');
                } catch (IOException e) {
                    throw new java.io.UncheckedIOException(e);
                }
            };

            out.accept("# Mo ta chi muc Lucene  (tao luc "
                    + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")) + ")");
            out.accept("");
            printOverview(reader, indexDir, out);

            FieldInfos fis = FieldInfos.getMergedFieldInfos(reader);
            printFields(reader, fis, out);

            String[] docId = buildDocIds(reader);
            out.accept("");
            out.accept("== Tu dien term (chi muc nguoc)  |  term -> [tai lieu chua no]"
                    + "  (df = so tai lieu, tf = tong so lan) ==");
            for (FieldInfo fi : fis) {
                if (fi.getIndexOptions() == IndexOptions.NONE) continue;
                dumpTerms(reader, docId, fi.name, REPORT_TERM_CAP, out);
            }

            out.accept("");
            out.accept("== Tai lieu (stored fields) ==");
            dumpDocs(reader, out);
        }
    }

    // ---------------------------------------------------------------- phan dung chung

    private interface Line extends java.util.function.Consumer<String> {}

    private static void printOverview(DirectoryReader reader, Path indexDir, Line out) throws IOException {
        out.accept("Chi muc     : " + indexDir.toAbsolutePath());
        out.accept("So tai lieu : " + reader.numDocs() + "  (da xoa: " + reader.numDeletedDocs() + ")");
        out.accept("So segment  : " + reader.leaves().size());
        out.accept("Dung luong  : " + dirSizeKb(indexDir) + " KB");
    }

    private static void printFields(DirectoryReader reader, FieldInfos fis, Line out) throws IOException {
        Set<String> stored = new LinkedHashSet<>();
        if (reader.numDocs() > 0) {
            reader.storedFields().document(0).forEach(f -> stored.add(f.name()));
        }
        out.accept("");
        out.accept("== Field ==");
        for (FieldInfo fi : fis) {
            out.accept(String.format(
                    "  %-10s | index=%-33s | stored=%-3s | norms=%-3s | docvalues=%-8s | points=%d",
                    fi.name,
                    fi.getIndexOptions(),
                    stored.contains(fi.name) ? "co" : "-",
                    fi.hasNorms() ? "co" : "-",
                    fi.getDocValuesType(),
                    fi.getPointDimensionCount()));
        }
        out.accept("  (index=NONE: field chi duoc luu, khong danh chi muc)");
    }

    private static void dumpTerms(DirectoryReader reader, String[] docId, String field,
                                  int limit, Line out) throws IOException {
        Terms terms = MultiTerms.getTerms(reader, field);
        if (terms == null) {
            return;
        }
        out.accept("");
        out.accept(String.format("  [%s]  so term = %s,  tong token = %d",
                field, terms.size() < 0 ? "?" : terms.size(), terms.getSumTotalTermFreq()));
        TermsEnum te = terms.iterator();
        PostingsEnum pe = null;
        BytesRef t;
        int n = 0;
        while ((t = te.next()) != null) {
            if (limit > 0 && n >= limit) {
                out.accept("      ... (con nua)");
                break;
            }
            pe = te.postings(pe, PostingsEnum.FREQS);
            StringBuilder docs = new StringBuilder();
            int shown = 0;
            int doc;
            while ((doc = pe.nextDoc()) != PostingsEnum.NO_MORE_DOCS) {
                if (shown == DOC_LIST_CAP) {
                    docs.append(", ...");
                    break;
                }
                if (shown > 0) {
                    docs.append(", ");
                }
                docs.append(doc < docId.length && docId[doc] != null ? docId[doc] : "#" + doc);
                int f = pe.freq();
                if (f > 1) {
                    docs.append('(').append(f).append(')');
                }
                shown++;
            }
            out.accept(String.format("      %-26s df=%-3d tf=%-3d -> [%s]",
                    t.utf8ToString(), te.docFreq(), te.totalTermFreq(), docs));
            n++;
        }
    }

    /** Bang tra doc# -> gia tri field "id" (de in ten tai lieu thay vi so noi bo). */
    private static String[] buildDocIds(DirectoryReader reader) throws IOException {
        String[] ids = new String[reader.maxDoc()];
        StoredFields sf = reader.storedFields();
        for (int i = 0; i < ids.length; i++) {
            ids[i] = sf.document(i).get("id");
        }
        return ids;
    }

    private static void dumpDocs(DirectoryReader reader, Line out) throws IOException {
        StoredFields sf = reader.storedFields();
        for (int d = 0; d < reader.maxDoc(); d++) {
            out.accept("  doc #" + d);
            for (var f : sf.document(d)) {
                String v = f.stringValue();
                if (v == null) {
                    v = "(non-string)";
                } else {
                    v = v.replaceAll("\\s+", " ").strip();
                    if (v.length() > PREVIEW) {
                        v = v.substring(0, PREVIEW) + " ...";
                    }
                }
                out.accept(String.format("      %-9s = %s", f.name(), v));
            }
        }
    }

    private static void dumpPostings(DirectoryReader reader, String fieldColonTerm) throws IOException {
        int c = fieldColonTerm.indexOf(':');
        if (c < 0) {
            System.err.println("--postings can dang <field>:<term>, vd content:lucene");
            return;
        }
        String field = fieldColonTerm.substring(0, c);
        String term = fieldColonTerm.substring(c + 1);

        System.out.println();
        System.out.println("== Postings cho " + field + ":" + term + " ==");
        PostingsEnum pe = MultiTerms.getTermPostingsEnum(
                reader, field, new BytesRef(term), PostingsEnum.POSITIONS);
        if (pe == null) {
            System.out.println("  (term khong co trong chi muc)");
            return;
        }
        StoredFields sf = reader.storedFields();
        int doc;
        while ((doc = pe.nextDoc()) != PostingsEnum.NO_MORE_DOCS) {
            int freq = pe.freq();
            List<Integer> pos = new ArrayList<>();
            for (int i = 0; i < freq; i++) {
                pos.add(pe.nextPosition());
            }
            System.out.printf("  doc #%-2d [%s]  freq=%d  vi_tri=%s%n",
                    doc, sf.document(doc).get("id"), freq, pos);
        }
    }

    private static long dirSizeKb(Path dir) {
        try (Stream<Path> s = Files.list(dir)) {
            long bytes = s.filter(Files::isRegularFile).mapToLong(p -> {
                try {
                    return Files.size(p);
                } catch (IOException e) {
                    return 0L;
                }
            }).sum();
            return (bytes + 1023) / 1024;
        } catch (IOException e) {
            return -1;
        }
    }
}
