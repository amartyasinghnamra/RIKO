/* ============================================================
   RIKO FRONTEND
   ============================================================ */

const actionBtn = document.getElementById("actionBtn");
const actionText = document.getElementById("actionText");
const stateDot = document.getElementById("stateDot");
const stateText = document.getElementById("stateText");
const closeBtn = document.getElementById("closeBtn");

let micState = "unknown";
let busy = false;


/* ============================================================
   MIC STATE UI
   ============================================================ */

function setMicState(state) {

    micState = state;

    actionBtn.classList.remove(
        "listening",
        "thinking",
        "speaking"
    );

    stateDot.className = "state-dot";

    if (state === "on") {

        actionBtn.classList.add("listening");
        stateDot.classList.add("listening");

        stateText.textContent = "MIC ON";
        actionText.textContent = "Stop";

    } else if (state === "off") {

        stateText.textContent = "MIC OFF";
        actionText.textContent = "Start";

    } else {

        stateDot.classList.add("unknown");

        stateText.textContent = "Disconnected";
        actionText.textContent = "Start";
    }
}


/* ============================================================
   START / STOP
   ============================================================ */

actionBtn.addEventListener("click", async () => {

    if (busy)
        return;

    busy = true;
    actionBtn.disabled = true;

    try {

        if (micState === "on") {

            console.log("[Riko] STOP");

            await stopRecording();

        } else {

            console.log("[Riko] START");

            await startRecording();
        }

        /*
         * Don't manually guess the resulting state.
         * Ask the backend for the truth.
         */

        await syncMicState();

    } catch (error) {

        console.error(
            "[Riko] Control error:",
            error
        );

        /*
         * Backend couldn't be reached.
         */
        setMicState("unknown");

    } finally {

        busy = false;
        actionBtn.disabled = false;
    }
});


/* ============================================================
   BACKEND STATE
   ============================================================ */

async function syncMicState() {

    try {

        const status = await getStatus();

        if (
            typeof status.recording_enabled !== "boolean"
        ) {
            setMicState("unknown");
            return;
        }

        setMicState(
            status.recording_enabled
                ? "on"
                : "off"
        );

    } catch (error) {

        setMicState("unknown");
    }
}


/* ============================================================
   CLOSE UI ONLY
   ============================================================ */

closeBtn.addEventListener("click", () => {

    const scene =
        document.getElementById("scene");

    scene.style.opacity = "0";
    scene.style.transform = "scale(0.97)";

    setTimeout(() => {

        scene.style.display = "none";

    }, 250);
});


/* ============================================================
   YOUTUBE-STYLE CLOSED CAPTIONS
   ============================================================ */

const CaptionSystem = (() => {

    let enabled = false;

    function create() {

        if (
            document.getElementById(
                "riko-captions"
            )
        ) {
            return;
        }

        const el =
            document.createElement("div");

        el.id = "riko-captions";

        el.innerHTML =
            '<span id="riko-caption-text"></span>';

        document.body.appendChild(el);
    }

    function toggle() {

        enabled = !enabled;

        const el =
            document.getElementById(
                "riko-captions"
            );

        if (el) {

            el.classList.toggle(
                "enabled",
                enabled
            );
        }

        console.log(
            "[Riko] Captions:",
            enabled ? "ON" : "OFF"
        );
    }

    function show(text) {

        if (!enabled)
            return;

        const el =
            document.getElementById(
                "riko-caption-text"
            );

        if (el)
            el.textContent = text;
    }

    function clear() {

        const el =
            document.getElementById(
                "riko-caption-text"
            );

        if (el)
            el.textContent = "";
    }

    return {
        create,
        toggle,
        show,
        clear
    };
})();


CaptionSystem.create();


/* ============================================================
   C = CAPTIONS ON / OFF
   ============================================================ */

document.addEventListener(
    "keydown",
    event => {

        if (
            event.key.toLowerCase() !== "c"
        ) {
            return;
        }

        const target = event.target;

        if (
            target instanceof HTMLInputElement ||
            target instanceof HTMLTextAreaElement ||
            target.isContentEditable
        ) {
            return;
        }

        event.preventDefault();

        CaptionSystem.toggle();
    }
);


/* ============================================================
   INITIAL STATE
   ============================================================ */

setMicState("unknown");

syncMicState();


/*
 * Backend remains the source of truth.
 * Poll only while the page is alive.
 */

setInterval(
    syncMicState,
    500
);
