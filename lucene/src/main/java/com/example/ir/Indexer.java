package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;

/** Tao chi muc Lucene tren dia tu danh sach document. */
public final class Indexer {

    private Indexer() {}

    public static int build(Path indexDir, List<DocLoader.Doc> docs, Analyzer analyzer) throws IOException {
        try (Directory dir = FSDirectory.open(indexDir)) {
            IndexWriterConfig cfg = new IndexWriterConfig(analyzer);
            cfg.setOpenMode(IndexWriterConfig.OpenMode.CREATE); // tao moi moi lan chay
            try (IndexWriter writer = new IndexWriter(dir, cfg)) {
                for (DocLoader.Doc d : docs) {
                    writer.addDocument(toLuceneDoc(d));
                }
                writer.commit();
                return writer.getDocStats().numDocs;
            }
        }
    }

    private static Document toLuceneDoc(DocLoader.Doc d) {
        Document doc = new Document();
        // id: khong phan tich, luu lai de hien thi / tra cuu
        doc.add(new StringField("id", d.id(), Field.Store.YES));
        doc.add(new StoredField("path", d.path().toString()));
        // filename: co phan tich (co the tim theo ten file)
        doc.add(new TextField("filename", d.path().getFileName().toString(), Field.Store.YES));
        // content: phan tich + tim kiem, VA luu lai ban goc.
        // Luu ban goc de Highlighter cat duoc doan chua tu khoa (KWIC) hien thi.
        doc.add(new TextField("content", d.content(), Field.Store.YES));
        return doc;
    }
}
