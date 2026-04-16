// CodeMirror 6 setup — loaded as ES module
// Uses import map (defined in index.html) so all packages share deps

import { basicSetup, EditorView } from 'codemirror';
import { StreamLanguage } from '@codemirror/language';
import { stex } from '@codemirror/legacy-modes/mode/stex';

let editorView = null;
let onChangeCallback = null;
let debounceTimer = null;

const latexLang = StreamLanguage.define(stex);

window.cmEditor = {
  init: function(parentEl, content, onChange) {
    onChangeCallback = onChange;
    if (editorView) { editorView.destroy(); editorView = null; }

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged && onChangeCallback) {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => onChangeCallback(editorView.state.doc.toString()), 600);
      }
    });

    editorView = new EditorView({
      doc: content,
      extensions: [
        basicSetup,
        latexLang,
        EditorView.lineWrapping,
        updateListener,
        EditorView.theme({
          '&': { fontSize: '15px', height: '100%', backgroundColor: '#ffffff' },
          '.cm-scroller': { overflow: 'auto', fontFamily: "'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace", fontWeight: '500' },
          '.cm-content': { padding: '8px 0' },
          '.cm-gutters': { backgroundColor: '#f8f8f8', borderRight: '1px solid #ddd' },
        }),
      ],
      parent: parentEl,
    });
    console.log('CodeMirror editor initialized with LaTeX highlighting');
  },

  getContent: function() {
    if (editorView) return editorView.state.doc.toString();
    var ta = document.querySelector('#review-cm-editor textarea');
    return ta ? ta.value : '';
  },

  highlightRange: function(from, to) {
    if (!editorView) return;
    var docLen = editorView.state.doc.length;
    if (from < 0) from = 0;
    if (to > docLen) to = docLen;
    if (from > docLen) from = docLen;

    editorView.dispatch({
      selection: { anchor: from, head: to },
      scrollIntoView: false,
    });

    // Scroll highlighted range to center of editor
    var coords = editorView.coordsAtPos(from);
    var scroller = editorView.scrollDOM;
    if (coords && scroller) {
      var scrollerRect = scroller.getBoundingClientRect();
      var targetY = coords.top - scrollerRect.top + scroller.scrollTop;
      var centerOffset = scroller.clientHeight / 2;
      scroller.scrollTo({ top: Math.max(0, targetY - centerOffset), behavior: 'smooth' });
    }

    editorView.focus();
  },

  setCiteRanges: function(ranges) {
    // No-op — selection highlight handles cites
  },

  isReady: function() {
    return editorView !== null || document.querySelector('#review-cm-editor textarea') !== null;
  },

  destroy: function() {
    if (editorView) { editorView.destroy(); editorView = null; }
  }
};

console.log('cm-setup.js module loaded');
