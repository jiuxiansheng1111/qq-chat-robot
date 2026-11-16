'use strict';
const path = require("node:path");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "../../..");
module.exports = () => createRequire(path.join(root, "data/netease/nim-runtime/package.json"))("node-nim");
