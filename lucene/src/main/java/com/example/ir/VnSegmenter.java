package com.example.ir;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Boc bo tach tu tieng Viet VnCoreNLP (chi dung annotator "wseg" = word segmentation).
 *
 * Nap DONG tu &lt;vnHome&gt;/lib/VnCoreNLP-1.2.jar bang URLClassLoader, nen du an van
 * build/chay binh thuong khi CHUA cai VnCoreNLP. Model doc tu
 * &lt;vnHome&gt;/models/wordsegmenter/{wordsegmenter.rdr, vi-vocab}.
 *
 * Cai dat: bash scripts/setup-vncorenlp.sh
 */
final class VnSegmenter implements AutoCloseable {

    static final String JAR_REL = "lib/VnCoreNLP-1.2.jar";
    static final String RDR_REL = "models/wordsegmenter/wordsegmenter.rdr";
    static final String VOCAB_REL = "models/wordsegmenter/vi-vocab";

    private final URLClassLoader loader;
    private final Object pipeline;           // vn.pipeline.VnCoreNLP
    private final Class<?> annotationClass;  // vn.pipeline.Annotation
    private final Method annotate;           // VnCoreNLP.annotate(Annotation)
    private final Method getSentences;       // Annotation.getSentences()
    private final Method getWords;           // Sentence.getWords()
    private final Method getForm;            // Word.getForm()

    /** Cache: Highlighter goi tach tu lai nhieu lan tren cung 1 tai lieu. */
    private final Map<String, List<String>> cache = Collections.synchronizedMap(
            new LinkedHashMap<>(128, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, List<String>> e) {
                    return size() > 1024;
                }
            });

    private VnSegmenter(URLClassLoader loader, Object pipeline, Class<?> annotationClass,
                        Method annotate, Method getSentences, Method getWords, Method getForm) {
        this.loader = loader;
        this.pipeline = pipeline;
        this.annotationClass = annotationClass;
        this.annotate = annotate;
        this.getSentences = getSentences;
        this.getWords = getWords;
        this.getForm = getForm;
    }

    /** true neu &lt;vnHome&gt; co du jar + 2 file model. */
    static boolean isAvailable(Path vnHome) {
        for (String rel : new String[]{JAR_REL, RDR_REL, VOCAB_REL}) {
            if (!Files.isReadable(vnHome.resolve(rel))) {
                return false;
            }
        }
        return true;
    }

    static String missingHint(Path vnHome) {
        StringBuilder sb = new StringBuilder("thieu file cua VnCoreNLP tai " + vnHome.toAbsolutePath() + ":");
        for (String rel : new String[]{JAR_REL, RDR_REL, VOCAB_REL}) {
            if (!Files.isReadable(vnHome.resolve(rel))) {
                sb.append("\n    - ").append(rel);
            }
        }
        sb.append("\n  Chay:  bash scripts/setup-vncorenlp.sh");
        return sb.toString();
    }

    /** Khoi tao pipeline "wseg". Nem IOException kem huong dan neu thieu jar/model. */
    static VnSegmenter create(Path vnHome) throws IOException {
        if (!isAvailable(vnHome)) {
            throw new IOException(missingHint(vnHome));
        }
        try {
            URL jarUrl = vnHome.resolve(JAR_REL).toUri().toURL();
            URLClassLoader cl = new URLClassLoader(new URL[]{jarUrl}, VnSegmenter.class.getClassLoader());

            // BAT BUOC: set Utils.jarDir TRUOC khi khoi tao VnCoreNLP -> tim dung thu muc model.
            Class<?> utils = Class.forName("vn.pipeline.Utils", true, cl);
            utils.getField("jarDir").set(null, vnHome.toAbsolutePath().toString());

            Class<?> vnClass = Class.forName("vn.pipeline.VnCoreNLP", true, cl);
            Object pipeline = vnClass.getConstructor(String[].class)
                    .newInstance((Object) new String[]{"wseg"});

            Class<?> annClass = Class.forName("vn.pipeline.Annotation", true, cl);
            Class<?> sentClass = Class.forName("vn.pipeline.Sentence", true, cl);
            Class<?> wordClass = Class.forName("vn.pipeline.Word", true, cl);

            return new VnSegmenter(cl, pipeline, annClass,
                    vnClass.getMethod("annotate", annClass),
                    annClass.getMethod("getSentences"),
                    sentClass.getMethod("getWords"),
                    wordClass.getMethod("getForm"));
        } catch (ReflectiveOperationException e) {
            throw new IOException("khong nap duoc VnCoreNLP: " + e, e);
        }
    }

    /**
     * Tach 1 doan van ban thanh danh sach "tu". Tu nhieu am tiet duoc noi bang '_'
     * (vd. "cong_nghe_thong_tin"); dau cau la token rieng.
     */
    List<String> segment(String text) {
        if (text == null || text.isBlank()) {
            return List.of();
        }
        List<String> cached = cache.get(text);
        if (cached != null) {
            return cached;
        }
        List<String> out = new ArrayList<>();
        try {
            synchronized (pipeline) {
                Object ann = annotationClass.getConstructor(String.class).newInstance(text);
                annotate.invoke(pipeline, ann);
                List<?> sentences = (List<?>) getSentences.invoke(ann);
                if (sentences != null) {
                    for (Object s : sentences) {
                        for (Object w : (List<?>) getWords.invoke(s)) {
                            out.add((String) getForm.invoke(w));
                        }
                    }
                }
            }
        } catch (ReflectiveOperationException e) {
            throw new UncheckedIOException(new IOException("loi tach tu VnCoreNLP: " + e, e));
        }
        List<String> result = List.copyOf(out);
        cache.put(text, result);
        return result;
    }

    @Override
    public void close() throws IOException {
        loader.close();
    }
}
