import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

function collect(folder) {
  return fs.readdirSync(folder, { withFileTypes: true }).flatMap(entry => {
    const name = path.join(folder, entry.name);
    return entry.isDirectory() ? collect(name) : name.endsWith('.tsx') ? [name] : [];
  });
}

const files = process.argv.slice(2);
if (!files.length) files.push('App.tsx', ...collect('src/components'), ...collect('src/screens'));
let errors = 0;
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const componentName = path.basename(file, '.tsx');
  const fail = message => { console.error(`${file}: ${message}`); errors += 1; };
  const props = tree.statements.find(node => ts.isInterfaceDeclaration(node) && node.name.text === `${componentName}Props`);
  if (!props || !props.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) {
    fail(`Export ${componentName}Props.`);
  } else {
    for (const member of props.members) {
      if (!member.modifiers?.some(m => m.kind === ts.SyntaxKind.ReadonlyKeyword)) fail('Every props member must be readonly.');
    }
  }
  if (/#(?:[\da-f]{3,8})\b|rgba?\(/i.test(source)) fail('Use theme color tokens.');
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const name = node.tagName.getText(tree);
      if (/^[a-z]/.test(name)) fail(`Use a React Native primitive instead of ${name}.`);
      if (['Pressable', 'TextInput'].includes(name)) {
        for (const attr of ['accessibilityLabel', 'accessibilityRole']) {
          if (!node.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.getText(tree) === attr)) fail(`${name} needs ${attr}.`);
        }
      }
      if (name === 'Image' && !node.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.getText(tree) === 'accessibilityLabel')) fail('Image needs accessibilityLabel.');
    }
    if (ts.isJsxText(node) && node.text.trim()) fail('Move static UI copy into src/data/mockData.ts.');
    ts.forEachChild(node, visit);
  }
  visit(tree);
}
if (errors) process.exitCode = 1;
else console.log(`Component conventions passed for ${files.length} files.`);
