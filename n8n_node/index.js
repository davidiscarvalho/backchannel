// n8n entry: the package.json `n8n` field points at the compiled output
// in dist/. This file exists so older n8n versions that probe the package
// main don't error out.
module.exports = {};
