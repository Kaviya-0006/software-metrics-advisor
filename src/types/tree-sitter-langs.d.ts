// Ambient typings for grammar packages that don't ship their own .d.ts.
declare module 'tree-sitter-java' {
  import type Parser from 'tree-sitter';
  const language: Parser.Language;
  export default language;
}
declare module 'tree-sitter-python' {
  import type Parser from 'tree-sitter';
  const language: Parser.Language;
  export default language;
}
declare module 'tree-sitter-javascript' {
  import type Parser from 'tree-sitter';
  const language: Parser.Language;
  export default language;
}
