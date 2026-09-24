package com.example.ir;

import org.apache.lucene.analysis.Analyzer;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Che do phu "segment": nhap 1 cau tu console, in ra ket qua TACH TU (word segmentation
 * cua VnCoreNLP) - tu nhieu am tiet duoc noi bang '_', vd "tri_tue_nhan_tao".
 *
 * Day la buoc dau tien cua VietnameseAnalyzer, TRUOC khi ha chu, bo tu dung/dau cau
 * va bo dau, nen ket qua khac voi token thuc su duoc lap chi muc.
 */
public final class SegmentRunner {

    private SegmentRunner() {}

    public static void run(Analyzer analyzer) throws IOException {
        if (!(analyzer instanceof VietnameseAnalyzer va)) {
            System.err.println("Che do 'segment' can analyzer 'vietnamese' (VnCoreNLP) de tach tu.");
            System.err.println("-> Chay lai voi --analyzer vietnamese (mac dinh) va da cai VnCoreNLP.");
            return;
        }

        System.out.println("Nhap 1 cau roi Enter de xem ket qua tach tu. Go ':q' (hoac Ctrl+D) de thoat.");
        try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            while (true) {
                System.out.print("\ncau> ");
                System.out.flush();
                String line = in.readLine();
                if (line == null) break; // Ctrl+D / het input
                line = line.strip();
                if (line.isEmpty()) continue;
                if (line.equals(":q") || line.equals(":quit") || line.equals("exit")) break;

                List<String> words = va.segment(line);
                if (words.isEmpty()) {
                    System.out.println("  (khong tach duoc tu)");
                    continue;
                }
                System.out.println("  So tu: " + words.size());
                for (int i = 0; i < words.size(); i++) {
                    System.out.printf("  %2d. %s%n", i + 1, words.get(i));
                }
                System.out.println("  => " + String.join(" | ", words));
            }
        }
        System.out.println("\nKet thuc.");
    }
}
