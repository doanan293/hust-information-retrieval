package com.example.ir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;

/**
 * Doc file truy van. Moi dong 1 truy van.
 * - Bo qua dong trong va dong bat dau bang '#'.
 * - Neu dong co ky tu TAB: phan truoc TAB la id, phan sau la noi dung.
 * - Neu khong: tu danh id "Q1", "Q2", ...
 */
public final class QueryLoader {

    /**
     * text duoc chuan hoa ve Unicode NFC (dung 1 ky tu dung san cho moi nguyen am co
     * dau, vd "ô" thay vi "o" + dau moc rieng). Ly do: van ban go/dan tren Linux hay
     * ra dang NFD (ky tu goc + dau ket hop rieng); ASCIIFoldingFilter chi bo duoc dau
     * cua ky tu NFC nen truy van NFD se KHONG khop tai liệu (da duoc luu/tach tu o
     * dang NFC) -> 0 ket qua du go dung tu. Chuan hoa tai day de moi noi tao Query
     * (batch lan interactive) deu duoc bao ve nhu nhau.
     */
    public record Query(String id, String text) {
        public Query {
            text = Normalizer.normalize(text, Normalizer.Form.NFC);
        }
    }

    private QueryLoader() {}

    public static List<Query> load(Path queriesFile) throws IOException {
        if (!Files.isRegularFile(queriesFile)) {
            throw new IOException("File truy van khong ton tai: " + queriesFile.toAbsolutePath());
        }
        List<Query> queries = new ArrayList<>();
        List<String> lines = Files.readAllLines(queriesFile, StandardCharsets.UTF_8);
        int auto = 0;
        for (String raw : lines) {
            String line = raw.strip();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            int tab = line.indexOf('\t');
            if (tab > 0) {
                queries.add(new Query(line.substring(0, tab).strip(), line.substring(tab + 1).strip()));
            } else {
                queries.add(new Query("Q" + (++auto), line));
            }
        }
        return queries;
    }
}
