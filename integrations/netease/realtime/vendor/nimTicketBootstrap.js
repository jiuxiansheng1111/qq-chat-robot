import loadNim from "../load-nim.cjs";
import { mkdir } from "node:fs/promises";
function asRecord(value) {
    return value && typeof value === "object" ? value : {};
}
function readNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}
async function run(request) {
    await mkdir(request.dataDir, {
        recursive: true
    });
    const imported = await loadNim();
    const candidate = imported.default ?? imported;
    const nim = candidate;
    if (typeof nim.NIMClient !== "function" || typeof nim.NIMPlugin !== "function") {
        throw new Error("node-nim NIMClient/NIMPlugin exports are unavailable");
    }
    const client = new nim.NIMClient();
    const plugin = new nim.NIMPlugin();
    const config = {
        database_encrypt_key_: request.appKey,
        use_https_: true,
        sdk_log_level_: 2
    };
    if (!client.init(request.appKey, request.dataDir + "/", "", config)) {
        throw new Error("NIM bootstrap client initialization failed");
    }
    client.initEventHandlers();
    plugin.initEventHandlers();
    const [loginResult] = await client.login(request.appKey, request.accId, request.token, null, "");
    const loginCode = readNumber(asRecord(loginResult).res_code_);
    if (loginCode !== 200) {
        throw new Error("NIM login failed" + (loginCode === null ? "" : " code=" + loginCode));
    }
    return await plugin.chatRoomRequestEnterAsync(request.roomNumber, null, "");
}
let started = false;
process.on("message", (raw)=>{
    if (started) return;
    started = true;
    void run(raw).then((result)=>{
        process.send?.({
            ok: true,
            result
        });
    }).catch((error)=>{
        process.send?.({
            ok: false,
            error: error instanceof Error ? error.message : "unknown bootstrap error"
        });
    });
});
setInterval(()=>{}, 60_000);
