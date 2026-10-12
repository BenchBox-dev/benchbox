const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.env.COMMENT_POLICY_TYPESCRIPT || path.resolve(__dirname, '../results-explorer/node_modules/typescript'));

const REVIEWED_SQL_WRAPPERS = {queryRows: 0, queryRowsOnce: 0, queryWithTimeout: 1};

function scan(name, source) {
  const kind = name.endsWith('.tsx') || name.endsWith('.jsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const tree = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, kind);
  const line = pos => tree.getLineAndCharacterOfPosition(pos).line + 1;
  if (tree.parseDiagnostics.length) {
    return tree.parseDiagnostics.map(d => ({kind: 'coverage-error', line: line(d.start || 0),
      text: ts.flattenDiagnosticMessageText(d.messageText, '\n')}));
  }
  const protectedRanges = [];
  const scopes = [];
  const declarations = [];
  const calls = [];
  const imports = new Map();
  const namespaces = new Set();
  const pathModules = new Map();
  const sqlProperties = [];
  const assignments = [];
  const pathImports = new Map();
  const bindings = [];
  const childProcess = value => ['node:child_process', 'child_process'].includes(value);
  const findings = [];
  function visit(node, symbol = '') {
    if (ts.isFunctionLike(node) || ts.isClassLike(node)) {
      const owner = node.name || (ts.isVariableDeclaration(node.parent) ? node.parent.name : undefined);
      const name = owner ? owner.getText(tree) : '<anonymous>';
      symbol = [symbol, name].filter(Boolean).join('.');
      scopes.push({start: node.getStart(tree), end: node.end, symbol});
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) declarations.push(node);
    if ((ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) && node.name && ts.isIdentifier(node.name)) bindings.push(node);
    if (ts.isImportDeclaration(node) && ['path', 'node:path'].includes(node.moduleSpecifier.text) && node.importClause) {
      const clause = node.importClause;
      if (clause.name) pathModules.set(clause.name.text, node.moduleSpecifier.text);
      if (clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) pathModules.set(clause.namedBindings.name.text, node.moduleSpecifier.text);
      if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
        for (const element of clause.namedBindings.elements) pathImports.set(element.name.text, (element.propertyName || element.name).text);
      }
    }
    if (ts.isImportDeclaration(node) && childProcess(node.moduleSpecifier.text) && node.importClause) {
      const clause = node.importClause;
      if (clause.name) namespaces.add(clause.name.text);
      if (clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) namespaces.add(clause.namedBindings.name.text);
      if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
        for (const element of clause.namedBindings.elements) imports.set(element.name.text, (element.propertyName || element.name).text);
      }
    }
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer)
        && node.initializer.expression.getText(tree) === 'require' && node.initializer.arguments.length === 1
        && ts.isStringLiteral(node.initializer.arguments[0]) && childProcess(node.initializer.arguments[0].text)) {
      if (ts.isIdentifier(node.name)) namespaces.add(node.name.text);
      if (ts.isObjectBindingPattern(node.name)) {
        for (const element of node.name.elements) imports.set(element.name.getText(tree), (element.propertyName || element.name).getText(tree));
      }
    }
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer)
        && node.initializer.expression.getText(tree) === 'require' && node.initializer.arguments.length === 1
        && ts.isStringLiteral(node.initializer.arguments[0]) && ['path', 'node:path'].includes(node.initializer.arguments[0].text)) {
      if (ts.isIdentifier(node.name)) pathModules.set(node.name.text, node.initializer.arguments[0].text);
      if (ts.isObjectBindingPattern(node.name)) {
        for (const element of node.name.elements) pathImports.set(element.name.getText(tree), (element.propertyName || element.name).getText(tree));
      }
    }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) calls.push(node);
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken) assignments.push(node);
    if (ts.isPropertyAssignment(node) && node.name.getText(tree) === 'sql') sqlProperties.push(node.initializer);
    if (ts.isStringLiteralLike(node) || ts.isRegularExpressionLiteral(node) || ts.isJsxText(node)
        || node.kind === ts.SyntaxKind.TemplateHead || node.kind === ts.SyntaxKind.TemplateMiddle
        || node.kind === ts.SyntaxKind.TemplateTail) protectedRanges.push([node.getStart(tree), node.end]);
    ts.forEachChild(node, child => visit(child, symbol));
  }
  visit(tree);
  const symbolAt = pos => [...scopes].reverse().find(scope => scope.start <= pos && pos < scope.end)?.symbol || '';
  function enclosingScope(node) {
    for (let current = node.parent; current; current = current.parent) {
      if (ts.isBlock(current) || ts.isSourceFile(current) || ts.isFunctionLike(current)) return current;
    }
  }
  function resolve(node, seen = new Set()) {
    if (!node || seen.has(node)) return null;
    seen = new Set([...seen, node]);
    if (ts.isIdentifier(node)) {
      for (let scope = enclosingScope(node); scope; scope = enclosingScope(scope)) {
        const matches = declarations.filter(d => d.name.text === node.text && enclosingScope(d) === scope);
        if (matches.length === 1) return resolve(matches[0].initializer, seen);
        if (matches.length > 1) return null;
      }
      return null;
    }
    if (ts.isStringLiteralLike(node)) return node.text;
    if (ts.isTemplateExpression(node)) {
      return node.head.text + node.templateSpans.map(span => {
        const value = resolve(span.expression, seen);
        return (value === null ? '__expression__' : value) + span.literal.text;
      }).join('');
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = resolve(node.left, seen), right = resolve(node.right, seen);
      return left === null || right === null ? null : left + right;
    }
    if (ts.isPropertyAccessExpression(node) && node.getText(tree) === 'process.execPath' && !shadowed('process', node)) return 'node';
    if (ts.isCallExpression(node) && node.arguments.length && (isPathFunction(node.expression, 'resolve')
        || (isPathFunction(node.expression, 'join') && resolve(node.arguments[0], seen)?.startsWith('/')))) {
      const leaf = resolve(node.arguments.at(-1), seen);
      return leaf && !leaf.startsWith('-') ? '/' + leaf.replace(/\\/g, '/').split('/').filter(Boolean).at(-1) : null;
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const method = node.expression.name.text, receiver = node.expression.expression;
      if (method === 'stringify' && receiver.getText(tree) === 'JSON') return JSON.stringify('__expression__');
      if (method === 'trim') return resolve(receiver, seen)?.trim() ?? null;
      if (method === 'join' && ts.isArrayLiteralExpression(receiver)) {
        const parts = receiver.elements.map(element => resolve(element, seen));
        const separator = node.arguments.length ? resolve(node.arguments[0], seen) : ',';
        return parts.includes(null) || separator === null ? null : parts.join(separator);
      }
    }
    return null;
  }
  function shadowed(name, node, imported = false) {
    for (let scope = enclosingScope(node); scope; scope = enclosingScope(scope)) {
      if (imported && ts.isSourceFile(scope)) return false;
      if (bindings.some(binding => binding.name.text === name && enclosingScope(binding) === scope)) return true;
    }
    return false;
  }
  function isPathFunction(expression, method) {
    if (ts.isPropertyAccessExpression(expression)) {
      return expression.name.text === method && ['path', 'node:path'].includes(pathModules.get(expression.expression.getText(tree)));
    }
    return ts.isIdentifier(expression) && pathImports.get(expression.text) === method;
  }
  function parameterOf(node) {
    if (!ts.isIdentifier(node)) return null;
    for (let scope = enclosingScope(node); scope; scope = enclosingScope(scope)) {
      if (declarations.some(d => d.name.text === node.text && enclosingScope(d) === scope)) return null;
      if (ts.isFunctionLike(scope)) {
        const index = scope.parameters.findIndex(p => ts.isIdentifier(p.name) && p.name.text === node.text);
        if (index >= 0) return {fn: scope, index};
      }
    }
    return null;
  }
  function functionName(fn) {
    if (fn.name && ts.isIdentifier(fn.name)) return fn.name.text;
    if (ts.isVariableDeclaration(fn.parent) && ts.isIdentifier(fn.parent.name)) return fn.parent.name.text;
    return null;
  }
  function exported(fn) {
    const holder = ts.isFunctionDeclaration(fn) ? fn : ts.isVariableDeclaration(fn.parent) ? fn.parent.parent.parent : null;
    return Boolean(holder && holder.modifiers && holder.modifiers.some(m => m.kind === ts.SyntaxKind.ExportKeyword));
  }
  function callSiteArguments(parameter) {
    const name = functionName(parameter.fn);
    if (!name || exported(parameter.fn)) return null;
    return calls.filter(c => ts.isIdentifier(c.expression) && c.expression.text === name).map(c => c.arguments[parameter.index]);
  }
  function sinkPassThrough(parameter, method) {
    const holder = parameter.fn.parent;
    if (ts.isBinaryExpression(holder) && ts.isPropertyAccessExpression(holder.left) && holder.left.name.text === method) return true;
    const name = functionName(parameter.fn);
    return Boolean(name && REVIEWED_SQL_WRAPPERS[name] === parameter.index);
  }
  function arrays(node) {
    if (!node) return null;
    if (ts.isArrayLiteralExpression(node)) return [node];
    if (ts.isIdentifier(node)) {
      const parameter = parameterOf(node);
      if (parameter) {
        const sites = callSiteArguments(parameter);
        if (!sites) return null;
        const found = sites.map(arrays);
        return found.includes(null) ? null : found.flat();
      }
      for (let scope = enclosingScope(node); scope; scope = enclosingScope(scope)) {
        const matches = declarations.filter(d => d.name.text === node.text && enclosingScope(d) === scope);
        if (matches.length === 1) return arrays(matches[0].initializer);
        if (matches.length > 1) return null;
      }
    }
    return null;
  }
  const emitted = new Set();
  function payload(node, language, supplied, method) {
    if (supplied === undefined && resolve(node) === null) {
      const parameter = parameterOf(node);
      if (parameter && method && sinkPassThrough(parameter, method)) return;
      const sites = parameter && callSiteArguments(parameter);
      if (sites && sites.length && !sites.includes(undefined)) {
        for (const site of sites) payload(site, language, undefined, method);
        return;
      }
    }
    const key = `${node.getStart(tree)}:${language}`;
    if (emitted.has(key)) return;
    emitted.add(key);
    const text = supplied === undefined ? resolve(node) : supplied;
    if (language === 'javascript' && text !== null) {
      findings.push(...scan(name, text).map(f => ({...f, line: line(node.getStart(tree)) + f.line - 1,
        symbol: `${symbolAt(node.getStart(tree))}:payload:${f.symbol || ''}`, payload: f.payload || text})));
      return;
    }
    findings.push({kind: text === null ? 'coverage-error' : 'payload', line: line(node.getStart(tree)),
      symbol: symbolAt(node.getStart(tree)), language,
      text: text === null ? `unresolved executable ${language} payload: ${node.getText(tree)}` : text});
  }
  for (const value of sqlProperties) {
    const branches = ts.isConditionalExpression(value) ? [value.whenTrue, value.whenFalse] : [value];
    for (const branch of branches) {
      if (resolve(branch) !== null) payload(branch, 'sql');
    }
  }
  for (const call of calls) {
    const target = call.expression;
    const localName = ts.isPropertyAccessExpression(target) ? target.name.text : target.getText(tree);
    let method = localName;
    let processImport = false;
    if (ts.isIdentifier(target) && imports.has(target.text) && !shadowed(target.text, call, true)) {
      method = imports.get(target.text);
      processImport = true;
    }
    if (ts.isPropertyAccessExpression(target) && ts.isIdentifier(target.expression)
        && namespaces.has(target.expression.text) && !shadowed(target.expression.text, call, true)) processImport = true;
    const args = call.arguments || [];
    if (ts.isIdentifier(target) && target.text === 'eval' && !shadowed('eval', call) && args[0]) payload(args[0], 'javascript');
    if (ts.isIdentifier(target) && target.text === 'Function' && !shadowed('Function', call) && args.length) {
      const parts = [...args].map(argument => resolve(argument));
      const text = parts.includes(null) ? null : `function __generated__(${parts.slice(0, -1).join(',')}) {\n${parts.at(-1)}\n}`;
      payload(args.at(-1), 'javascript', text);
    }
    if (processImport && ['exec', 'execSync'].includes(method) && args[0]) payload(args[0], 'bash');
    if (['query', 'prepare', 'execute', 'executemany', 'sql'].includes(method) && call.arguments[0]) {
      if (!ts.isSpreadElement(call.arguments[0])) payload(call.arguments[0], 'sql', undefined, method);
      else if (!preparedReceiver(target)) {
        findings.push({kind: 'coverage-error', line: line(call.getStart(tree)), symbol: symbolAt(call.getStart(tree)),
          text: `unresolved executable sql payload: ${call.arguments[0].getText(tree)}`});
      }
    }
    const wrapperName = ts.isIdentifier(target) ? target.text : ts.isPropertyAccessExpression(target) ? target.name.text : null;
    if (wrapperName && Object.hasOwn(REVIEWED_SQL_WRAPPERS, wrapperName)) {
      const argument = call.arguments[REVIEWED_SQL_WRAPPERS[wrapperName]];
      if (argument && ts.isSpreadElement(argument)) {
        findings.push({kind: 'coverage-error', line: line(call.getStart(tree)), symbol: symbolAt(call.getStart(tree)),
          text: `unresolved executable sql payload: ${argument.getText(tree)}`});
      } else if (argument) payload(argument, 'sql', undefined, wrapperName);
    }
    if (['spawn', 'spawnSync', 'execFile', 'execFileSync'].includes(method) && call.arguments[0]) {
      const executable = resolve(call.arguments[0]);
      const argumentLists = arrays(call.arguments[1]);
      if (!executable || !argumentLists) {
        findings.push({kind: 'coverage-error', line: line(call.getStart(tree)), symbol: symbolAt(call.getStart(tree)),
          text: 'unresolved process arguments require an executable-payload adapter: '
            + `${call.expression.getText(tree)}(${[...call.arguments].slice(0, 2).map(a => a.getText(tree)).join(', ')})`});
        continue;
      }
      for (const args of argumentLists) processPayload(call, executable, [...args.elements]);
    }
  }
  function preparedReceiver(target) {
    if (!ts.isPropertyAccessExpression(target) || !ts.isIdentifier(target.expression)) return false;
    const name = target.expression.text;
    const sources = [];
    for (const node of declarations) if (node.name.text === name && node.initializer) sources.push(node.initializer);
    for (const call of assignments) if (call.left.getText(tree) === name) sources.push(call.right);
    const meaningful = sources.filter(source => source.kind !== ts.SyntaxKind.NullKeyword && source.getText(tree) !== 'undefined');
    return meaningful.length > 0 && meaningful.every(source => /\.prepare\(/.test(source.getText(tree)));
  }
  function processPayload(call, executable, elements) {
    const words = elements.map(element => ts.isSpreadElement(element) ? null : resolve(element));
    let program = path.basename(executable), start = 0;
    if (program === 'uv') {
      const interpreter = words.findIndex(word => word && /^(python[0-9.]*)$/.test(word));
      if (interpreter < 0) return;
      program = words[interpreter];
      start = interpreter + 1;
    }
    const language = /^python[0-9.]*$/.test(program) ? 'python' : program === 'node' ? 'javascript'
      : ['sh', 'bash', 'zsh'].includes(program) ? 'bash' : ['psql', 'duckdb', 'sqlite3'].includes(program) ? 'sql' : null;
    if (!language) return;
    for (let index = start; index < words.length; index++) {
      const word = words[index];
      if (word === null) {
        findings.push({kind: 'coverage-error', line: line(call.getStart(tree)), symbol: symbolAt(call.getStart(tree)),
          text: 'unresolved process arguments require an executable-payload adapter'});
        return;
      }
      if (['-c', '-e', '--eval', '--command'].includes(word)) {
        if (elements[index + 1]) payload(elements[index + 1], language);
        return;
      }
      if (!word.startsWith('-')) return;
    }
  }
  protectedRanges.sort((a, b) => a[0] - b[0]);
  let range = 0, pos = 0;
  while (pos < source.length) {
    while (range < protectedRanges.length && protectedRanges[range][1] <= pos) range++;
    if (range < protectedRanges.length && protectedRanges[range][0] <= pos) {
      pos = protectedRanges[range][1];
      continue;
    }
    let end;
    if (source.startsWith('//', pos) || (pos === 0 && source.startsWith('#!', pos))) {
      end = source.indexOf('\n', pos);
      if (end < 0) end = source.length;
    } else if (source.startsWith('/*', pos)) {
      end = source.indexOf('*/', pos + 2);
      if (end < 0) throw new Error('unterminated JavaScript comment');
      end += 2;
    } else {
      pos++;
      continue;
    }
    findings.push({kind: 'comment', line: line(pos), symbol: symbolAt(pos), text: source.slice(pos, end).replace(/\r$/, '')});
    pos = end;
  }
  return findings;
}

module.exports = {scan};
if (require.main === module) {
  const requests = JSON.parse(fs.readFileSync(0, 'utf8'));
  const result = Object.fromEntries(Object.entries(requests).map(([name, source]) => [name, scan(name, source)]));
  process.stdout.write(JSON.stringify(result));
}
