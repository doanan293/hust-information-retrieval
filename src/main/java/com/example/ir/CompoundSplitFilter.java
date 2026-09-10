package com.example.ir;

import org.apache.lucene.analysis.TokenFilter;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.analysis.tokenattributes.PositionIncrementAttribute;
import org.apache.lucene.analysis.tokenattributes.TypeAttribute;

import java.io.IOException;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Voi token la tu ghep "tri_tue_nhan_tao", phat ra:
 *   tri_tue_nhan_tao        (giu nguyen  - de khop dung TU va len diem)
 *   tri (posInc 0), tue (1), nhan (1), tao (1)   <- am tiet, LIEN TIEP nhau, type = SYLLABLE
 *
 * Nho am tiet nam lien tiep dung thu tu, phia truy van co the dung PhraseQuery tren
 * chuoi am tiet -> "dai hoc" chi khop noi co "dai" ngay truoc "hoc", KHONG khop
 * "sinh hoc" / "khoa hoc". Chinh xac hon nhieu so voi ghep OR tung am tiet.
 *
 * Tu ghep "chiem" cac vi tri cua am tiet (P .. P+n-1) nen token ke tiep tu dong
 * roi vao P+n, khong can chinh gi them.
 */
final class CompoundSplitFilter extends TokenFilter {

    static final String SYLLABLE_TYPE = "SYLLABLE";

    private final CharTermAttribute termAtt = addAttribute(CharTermAttribute.class);
    private final PositionIncrementAttribute posAtt = addAttribute(PositionIncrementAttribute.class);
    private final TypeAttribute typeAtt = addAttribute(TypeAttribute.class);
    private final Deque<String> pending = new ArrayDeque<>();
    private boolean firstPart;

    CompoundSplitFilter(TokenStream input) {
        super(input);
    }

    @Override
    public boolean incrementToken() throws IOException {
        if (!pending.isEmpty()) {
            String part = pending.poll();
            termAtt.setEmpty().append(part);
            posAtt.setPositionIncrement(firstPart ? 0 : 1); // am tiet dau chong len tu ghep
            typeAtt.setType(SYLLABLE_TYPE);
            firstPart = false;
            return true;
        }
        if (!input.incrementToken()) {
            return false;
        }
        String t = termAtt.toString();
        if (t.indexOf('_') > 0) {
            for (String p : t.split("_")) {
                if (!p.isEmpty()) {
                    pending.add(p);
                }
            }
            firstPart = true;
        }
        return true; // token hien tai giu nguyen la tu ghep
    }

    @Override
    public void reset() throws IOException {
        super.reset();
        pending.clear();
        firstPart = false;
    }
}
