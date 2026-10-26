// Web App playback is always enabled; website playback is optional.
function isPlaybackEnabled() {
    return isStandaloneApp() || window.BOX4ONE_CONFIG?.ENABLE_WEBSITE_PLAY === true;
}

const JOB_CODE_REGEX =
    /^[a-z0-9]{10}$/;


const homeView =
    document.getElementById(
        "homeView"
    );

const jobView =
    document.getElementById(
        "jobView"
    );

const queueView =
    document.getElementById(
        "queueView"
    );


const homeForm =
    document.getElementById(
        "homeForm"
    );

const mainInput =
    document.getElementById(
        "mainInput"
    );

const inputShell =
    document.querySelector(
        ".input-shell"
    );

const formatPanel =
    document.getElementById(
        "formatPanel"
    );

const formatButtons =
    document.querySelectorAll(
        ".format-button"
    );


const jobCodeElement =
    document.getElementById(
        "jobCode"
    );

const statusBadge =
    document.getElementById(
        "statusBadge"
    );

const statusTitle =
    document.getElementById(
        "statusTitle"
    );

const statusText =
    document.getElementById(
        "statusText"
    );

const progressNumbers =
    document.getElementById(
        "progressNumbers"
    );

const progressBarWrap =
    document.getElementById(
        "progressBarWrap"
    );

const progressBar =
    document.getElementById(
        "progressBar"
    );

const expirationBox =
    document.getElementById(
        "expirationBox"
    );

const expirationText =
    document.getElementById(
        "expirationText"
    );

const filesSection =
    document.getElementById(
        "filesSection"
    );

const filesCount =
    document.getElementById(
        "filesCount"
    );

const filesList =
    document.getElementById(
        "filesList"
    );

const downloadAllButton =
    document.getElementById(
        "downloadAllButton"
    );

const jobErrorBox =
    document.getElementById(
        "jobErrorBox"
    );

const jobErrorText =
    document.getElementById(
        "jobErrorText"
    );


const queueActive =
    document.getElementById(
        "queueActive"
    );

const queueWaiting =
    document.getElementById(
        "queueWaiting"
    );

const queueTotal =
    document.getElementById(
        "queueTotal"
    );

const queueList =
    document.getElementById(
        "queueList"
    );

const queuePagination =
    document.getElementById(
        "queuePagination"
    );

const queuePrevious =
    document.getElementById(
        "queuePrevious"
    );

const queueNext =
    document.getElementById(
        "queueNext"
    );

const queuePageText =
    document.getElementById(
        "queuePageText"
    );


let downloadAccessWord =
    sessionStorage.getItem(
        "box4one-download-access"
    );

let unlocked = Boolean(downloadAccessWord);
let accessMode = sessionStorage.getItem("box4one-access-mode");


let queueAccessWord =
    sessionStorage.getItem(
        "box4one-queue-access"
    );


let pendingUrl =
    null;

let pollTimer =
    null;

let expirationTimer =
    null;

let queueTimer =
    null;

let currentJob =
    null;

let currentQueuePage =
    1;


/* ===================================================== */
/* ENVIRONMENT */
/* ===================================================== */

function isStandaloneApp() {

    const iosStandalone =
        window.navigator
            .standalone
        === true;


    const displayStandalone =
        window.matchMedia(
            "(display-mode: standalone)"
        ).matches;


    return (
        iosStandalone
        ||
        displayStandalone
    );
}


function isIOSWebApp() {
    const ios = window.navigator.standalone === true
        || /iPhone|iPad|iPod/.test(window.navigator.userAgent || "")
        || (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);
    return ios && isStandaloneApp();
}

function prepareDownloadLink(link) {
    link.removeAttribute("target");
    link.removeAttribute("rel");
    if (isIOSWebApp()) {
        const url = new URL(link.href, window.location.origin);
        if (url.origin !== window.location.origin || url.protocol !== "https:") {
            throw new Error("Safari download requires a same-origin HTTPS URL");
        }
        // Only replace the leading protocol; preserve filenames/query parameters.
        link.href = "x-safari-https:" + url.href.slice("https:".length);
        link.removeAttribute("download");
    } else if (isStandaloneApp()) {
        link.setAttribute("download", "");
    }
}


/* ===================================================== */
/* ROUTING */
/* ===================================================== */

function getJobCodeFromPath() {

    const path =
        window.location.pathname;


    let match =
        path.match(
            /^\/([a-z0-9]{10})\/?$/
        );


    if (match) {

        return match[1];
    }


    match =
        path.match(
            /^\/job\/([a-z0-9]{10})\/?$/
        );


    if (match) {

        return match[1];
    }


    return null;
}


function isQueuePath() {

    return (
        window.location.pathname
        === "/queue"

        ||

        window.location.pathname
        === "/queue/"
    );
}


function openJob(
    jobCode
) {

    window.location.href =
        `/${jobCode.toLowerCase()}`;
}


/* ===================================================== */
/* API */
/* ===================================================== */

async function apiRequest(
    url,
    options = {}
) {

    const response =
        await fetch(
            url,
            {
                ...options,
                headers: {
                    ...options.headers,
                    ...(isStandaloneApp() ? {"X-Web-App": "1"} : {})
                }
            }
        );


    let data =
        null;


    try {

        data =
            await response.json();

    } catch {

        data =
            null;
    }


    if (!response.ok) {

        let message =
            `Request failed (${response.status})`;


        if (
            data
            && typeof data.detail
                === "string"
        ) {

            message =
                data.detail;
        }


        const error =
            new Error(
                message
            );


        error.status =
            response.status;


        throw error;
    }


    return data;
}


// Establish a browser identity before admission; values come from the backend.
const serviceLimitsReady = apiRequest('/api/limits').then(limits => {
    const element = document.getElementById('serviceLimits');
    if (element) element.textContent = `Лимит задания: ${limits.max_job_gb}ГБ, включая временные файлы.\n`
        + `Плейлист: до ${limits.video_playlist_limit} видео или ${limits.audio_playlist_limit} аудиофайлов.\n`
        + `Активных заданий: до ${limits.max_active_jobs_per_user}. Хранение файлов: ${limits.job_ttl_minutes} мин.`;
    return limits;
});
serviceLimitsReady.catch(() => {
    const element = document.getElementById('serviceLimits');
    if (element) element.textContent = 'Не удалось загрузить лимиты сервиса. Обновите страницу.';
});

/* ===================================================== */
/* ACCESS */
/* ===================================================== */

async function checkAccessWord(
    word
) {

    try {

        return await apiRequest(
            "/api/access",
            {
                method:
                    "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify(
                        {
                            word:
                                word
                        }
                    )
            }
        );

    } catch {

        return null;
    }
}


function enableDownloadMode(word) {

    downloadAccessWord = isStandaloneApp() ? null : word;
    accessMode = "download";
    sessionStorage.setItem("box4one-access-mode", accessMode);

    unlocked =
        true;


    sessionStorage.setItem(
        "box4one-download-access",
        isStandaloneApp() ? "" : word
    );


    inputShell.classList.add(
        "unlocked"
    );


    mainInput.value =
        "";


    document.getElementById("homeQueueLink").classList.add("hidden");
    mainInput.focus();
}


function enableQueueMode(
    word
) {

    accessMode = "queue";
    unlocked = true;
    queueAccessWord = isStandaloneApp() ? null : word;
    downloadAccessWord = isStandaloneApp() ? null : word;
    sessionStorage.setItem("box4one-access-mode", "queue");
    sessionStorage.setItem("box4one-download-access", downloadAccessWord || "");


    sessionStorage.setItem(
        "box4one-queue-access",
        queueAccessWord || ""
    );


    window.location.href =
        "/queue";
}


/* ===================================================== */
/* HOME */
/* ===================================================== */

function hideFormatPanel() {

    formatPanel.classList.remove(
        "visible"
    );


    pendingUrl =
        null;
}


function updateFormatPanelViewport() {
    const viewport = window.visualViewport;
    const inset = viewport
        ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
        : 0;
    formatPanel.style.setProperty("--keyboard-inset", `${inset}px`);
}

if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", updateFormatPanelViewport);
    window.visualViewport.addEventListener("scroll", updateFormatPanelViewport);
}
window.addEventListener("resize", updateFormatPanelViewport);

function showFormatPanel(url) {
    pendingUrl = url;
    // Input is complete; dismiss the keyboard before choosing a format.
    mainInput.blur();
    updateFormatPanelViewport();
    formatPanel.classList.add("visible");
}


function isJobCode(
    value
) {

    return JOB_CODE_REGEX.test(
        value.toLowerCase()
    );
}


function isValidUrl(
    value
) {

    try {

        const url =
            new URL(
                value
            );


        return (
            url.protocol
                === "http:"

            ||

            url.protocol
                === "https:"
        );

    } catch {

        return false;
    }
}


mainInput.addEventListener(
    "input",
    () => {

        hideFormatPanel();
    }
);


homeForm.addEventListener(
    "submit",
    async (
        event
    ) => {

        event.preventDefault();


        const value =
            mainInput
                .value
                .trim();


        if (!value) {
            return;
        }


        /*
         * ACCESS WORDS ARE ALWAYS CHECKED FIRST.
         *
         * This is intentionally independent of the current
         * unlocked state.
         *
         * - downloader access word:
         *     unlocks creation of new jobs
         *
         * - queue/admin access word:
         *     always opens /queue
         *
         * This check must remain before JOB_CODE_REGEX because
         * an access word may itself look like a 10-character
         * job code.
         */

        const access =
            await checkAccessWord(
                value
            );


        if (
            access
            && access.mode
                === "queue"
        ) {

            enableQueueMode(
                value
            );

            return;
        }


        if (
            access
            && access.mode
                === "download"
        ) {

            enableDownloadMode(value);

            return;
        }


        /*
         * EXISTING JOB CODES ARE ALWAYS AVAILABLE.
         *
         * Looking up an existing/active/completed job does not
         * require downloader unlock.
         */

        if (
            isJobCode(
                value
            )
        ) {

            openJob(
                value
            );

            return;
        }


        /*
         * Only creation of a NEW job from a URL requires
         * downloader access.
         */

        if (!unlocked) {

            return;
        }


        if (
            isValidUrl(
                value
            )
        ) {

            showFormatPanel(
                value
            );

            return;
        }
    }
);


formatButtons.forEach(
    (
        button
    ) => {

        button.addEventListener(
            "click",
            async () => {

                if (!pendingUrl) {

                    return;
                }


                const media =
                    button.dataset.media;


                const quality =
                    button.dataset.quality;


                const url =
                    pendingUrl;


                formatButtons.forEach(
                    (
                        item
                    ) => {

                        item.disabled =
                            true;
                    }
                );


                try {
                    await serviceLimitsReady;

                    const result =
                        await apiRequest(
                            "/api/jobs",
                            {
                                method:
                                    "POST",

                                headers: {
                                    "Content-Type":
                                        "application/json",
                                    "X-Download-Access":
                                        downloadAccessWord || ""
                                },

                                body:
                                    JSON.stringify(
                                        {
                                            source_url:
                                                url,

                                            media_type:
                                                media,

                                            quality:
                                                quality
                                        }
                                    )
                            }
                        );


                    openJob(
                        result.job_code
                    );


                } catch (error) {

                    if (error.status === 403) {
                        sessionStorage.removeItem("box4one-download-access");
                        sessionStorage.removeItem("box4one-unlocked");
                        downloadAccessWord = null;
                        unlocked = false;
                        accessMode = null;
                        sessionStorage.removeItem("box4one-access-mode");
                        inputShell.classList.remove("unlocked");
                        hideFormatPanel();
                        mainInput.value = "";
                        mainInput.focus();
                    }

                    formatButtons.forEach(
                        (
                            item
                        ) => {

                            item.disabled =
                                false;
                        }
                    );
                }
            }
        );
    }
);


document.addEventListener(
    "keydown",
    (
        event
    ) => {

        if (
            event.key
            === "Escape"
        ) {

            hideFormatPanel();
        }
    }
);


/* ===================================================== */
/* HELPERS */
/* ===================================================== */

function formatBytes(
    bytes
) {

    if (
        !Number.isFinite(
            bytes
        )

        ||

        bytes < 0
    ) {

        return "—";
    }


    const units = [
        "B",
        "KB",
        "MB",
        "GB"
    ];


    let value =
        bytes;


    let index =
        0;


    while (
        value >= 1024
        && index
            < units.length - 1
    ) {

        value /=
            1024;


        index +=
            1;
    }


    const digits =
        index === 0
            ? 0
            : value >= 100
                ? 0
                : value >= 10
                    ? 1
                    : 2;


    return (
        value.toFixed(
            digits
        )
        + " "
        + units[index]
    );
}


function parseUtcDate(
    value
) {

    if (!value) {

        return null;
    }


    let normalized =
        value;


    if (
        !normalized.endsWith(
            "Z"
        )

        &&

        !/[+-]\d\d:\d\d$/.test(
            normalized
        )
    ) {

        normalized +=
            "Z";
    }


    const date =
        new Date(
            normalized
        );


    if (
        Number.isNaN(
            date.getTime()
        )
    ) {

        return null;
    }


    return date;
}


function isExpiredDate(
    value
) {

    const date =
        parseUtcDate(
            value
        );


    if (!date) {

        return false;
    }


    return (
        date.getTime()
        <= Date.now()
    );
}


function formatRemaining(
    value
) {

    const date =
        parseUtcDate(
            value
        );


    if (!date) {

        return "—";
    }


    const seconds =
        Math.max(
            0,

            Math.floor(
                (
                    date.getTime()
                    - Date.now()
                )
                / 1000
            )
        );


    if (
        seconds <= 0
    ) {

        return "expired";
    }


    const hours =
        Math.floor(
            seconds
            / 3600
        );


    const minutes =
        Math.floor(
            (
                seconds
                % 3600
            )
            / 60
        );


    const secs =
        seconds
        % 60;


    if (
        hours > 0
    ) {

        return (
            `${hours}h ${minutes}m`
        );
    }


    if (
        minutes > 0
    ) {

        return (
            `${minutes}m ${secs}s`
        );
    }


    return (
        `${secs}s`
    );
}


function formatCreatedAt(
    value
) {

    const date =
        parseUtcDate(
            value
        );


    if (!date) {

        return "—";
    }


    return date.toLocaleString(
        undefined,
        {
            year:
                "numeric",

            month:
                "2-digit",

            day:
                "2-digit",

            hour:
                "2-digit",

            minute:
                "2-digit"
        }
    );
}


/* ===================================================== */
/* FILES */
/* ===================================================== */

function clearFiles() {

    filesSection.classList.add(
        "hidden"
    );


    filesList.innerHTML =
        "";


    filesCount.textContent =
        "0";


    downloadAllButton.classList.add(
        "hidden"
    );


    downloadAllButton.classList.remove(
        "busy"
    );


    downloadAllButton.textContent =
        "Download all";


    downloadAllButton.removeAttribute(
        "href"
    );


    downloadAllButton.removeAttribute(
        "target"
    );


    downloadAllButton.removeAttribute(
        "rel"
    );
}


function renderFiles(
    files,
    jobCode
) {

    filesList.innerHTML =
        "";


    filesCount.textContent =
        files.length;


    if (
        files.length > 1
    ) {

        downloadAllButton.href =
            `/api/jobs/${jobCode}/archive`;


        prepareDownloadLink(
            downloadAllButton
        );


        downloadAllButton.classList.remove(
            "hidden"
        );

    } else {

        downloadAllButton.classList.add(
            "hidden"
        );
    }


    files.forEach(
        (
            file
        ) => {

            const row =
                document.createElement(
                    "div"
                );


            row.className =
                "file-item";


            const name =
                document.createElement(
                    "div"
                );


            name.className =
                "file-name";


            name.textContent =
                file.filename;


            name.title =
                file.filename;


            const size =
                document.createElement(
                    "div"
                );


            size.className =
                "file-size";


            size.textContent =
                formatBytes(
                    file.size_bytes
                );


            const actions = document.createElement("div");
            actions.className = "file-actions";
            if (isPlaybackEnabled() && /\.(mp4|mp3)$/i.test(file.filename)) {
                const play = document.createElement("button");
                play.type = "button";
                play.className = "home-button file-icon-button";
                play.title = "Play";
                play.setAttribute("aria-label", `Play ${file.filename}`);
                play.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
                play.addEventListener("click", () => playInsideApp(file));
                actions.appendChild(play);
            }
            const link = document.createElement("a");
            link.className = "home-button file-icon-button";
            link.href = file.download_url;
            link.title = "Download";
            link.setAttribute("aria-label", `Download ${file.filename}`);
            link.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            prepareDownloadLink(link);
            link.addEventListener("click", () => {
                if (isStandaloneApp()) showDownloadNotice();
            });
            actions.appendChild(link);
            row.append(name, size, actions);

            filesList.appendChild(
                row
            );
        }
    );
}


downloadAllButton.addEventListener(
    "click",
    (
        event
    ) => {

        if (
            downloadAllButton
                .classList
                .contains(
                    "busy"
                )
        ) {

            event.preventDefault();

            return;
        }


        if (isStandaloneApp()) showDownloadNotice();
        downloadAllButton.classList.add(
            "busy"
        );


        downloadAllButton.textContent =
            "Starting…";


        window.setTimeout(
            () => {

                downloadAllButton.classList.remove(
                    "busy"
                );


                downloadAllButton.textContent =
                    "Download all";

            },
            5000
        );
    }
);


/* ===================================================== */
/* JOB */
/* ===================================================== */

function setStatus(
    badge,
    title,
    text
) {

    statusBadge.textContent =
        badge;


    statusTitle.textContent =
        title;


    statusText.textContent =
        text;
}


function renderExpired() {

    if (!currentJob) {

        return;
    }


    currentJob = {
        ...currentJob,

        status:
            "expired",

        files:
            [],
    };


    clearPoll();

    clearFiles();


    progressNumbers.classList.add(
        "hidden"
    );


    progressBarWrap.classList.add(
        "hidden"
    );


    expirationBox.classList.add(
        "hidden"
    );


    jobErrorBox.classList.add(
        "hidden"
    );


    setStatus(
        "Expired",
        "Expired",
        "Files are no longer available."
    );


    if (
        expirationTimer
        !== null
    ) {

        window.clearInterval(
            expirationTimer
        );


        expirationTimer =
            null;
    }
}


function updateExpiration() {

    if (
        !currentJob

        ||

        currentJob.status
            !== "completed"

        ||

        !currentJob.expires_at
    ) {

        expirationBox.classList.add(
            "hidden"
        );

        return;
    }


    if (
        isExpiredDate(
            currentJob.expires_at
        )
    ) {

        renderExpired();

        return;
    }


    expirationBox.classList.remove(
        "hidden"
    );


    expirationText.textContent =
        formatRemaining(
            currentJob.expires_at
        );
}


function renderJob(
    job
) {

    if (
        job.status
            === "completed"

        &&

        job.expires_at

        &&

        isExpiredDate(
            job.expires_at
        )
    ) {

        job = {
            ...job,

            status:
                "expired",

            files:
                [],
        };
    }


    currentJob =
        job;


    jobCodeElement.textContent =
        job.job_code;


    progressNumbers.classList.add(
        "hidden"
    );


    progressBarWrap.classList.add(
        "hidden"
    );


    clearFiles();


    expirationBox.classList.add(
        "hidden"
    );


    jobErrorBox.classList.add(
        "hidden"
    );


    switch (
        job.status
    ) {

        case "queued":

            setStatus(
                "Queued",
                "Waiting",
                "The job will start automatically."
            );

            break;


        case "downloading":
        case "processing": {

            setStatus(
                job.status,

                job.status
                    === "processing"
                    ? "Processing"
                    : "Downloading",

                ""
            );


            const total =
                Number(
                    job.total_items
                );


            const completed =
                Number(
                    job.completed_items
                );


            if (
                Number.isFinite(
                    total
                )

                &&

                total > 0
            ) {

                progressNumbers.classList.remove(
                    "hidden"
                );


                progressBarWrap.classList.remove(
                    "hidden"
                );


                progressNumbers.textContent =
                    `${completed} / ${total}`;


                const percentage =
                    Math.min(
                        100,

                        Math.max(
                            0,

                            completed
                            / total
                            * 100
                        )
                    );


                progressBar.style.width =
                    `${percentage}%`;
            }


            break;
        }


        case "completed":

            setStatus(
                "Completed",
                "Ready",
                ""
            );


            progressNumbers.classList.remove(
                "hidden"
            );


            progressBarWrap.classList.remove(
                "hidden"
            );


            progressNumbers.textContent =
                `${job.completed_items} / ${job.total_items}`;


            progressBar.style.width =
                "100%";


            if (
                Array.isArray(
                    job.files
                )

                &&

                job.files.length > 0
            ) {

                filesSection.classList.remove(
                    "hidden"
                );


                renderFiles(
                    job.files,
                    job.job_code
                );
            }


            updateExpiration();

            break;


        case "expired":

            renderExpired();

            break;


        case "failed":

            setStatus(
                "Failed",
                "Unable to complete",
                ""
            );


            jobErrorBox.classList.remove(
                "hidden"
            );


            jobErrorText.textContent =
                job.error_message
                || "Unknown error.";

            break;


        default:

            setStatus(
                job.status,
                "Working",
                ""
            );
    }
}


/* ===================================================== */
/* JOB POLLING */
/* ===================================================== */

async function loadCurrentJob() {

    const jobCode =
        getJobCodeFromPath();


    if (!jobCode) {

        return;
    }


    try {

        const job =
            await apiRequest(
                `/api/jobs/${jobCode}`
            );


        renderJob(
            job
        );


        if (
            job.status
                === "queued"

            ||

            job.status
                === "downloading"

            ||

            job.status
                === "processing"
        ) {

            schedulePoll();

        } else {

            clearPoll();
        }


    } catch (
        error
    ) {

        clearPoll();


        if (
            error.status
                === 404
        ) {

            clearFiles();


            setStatus(
                "Not found",
                "Job not found",
                ""
            );


            return;
        }


        setStatus(
            "Error",
            "Unable to load",
            error.message
        );


        schedulePoll();
    }
}


function schedulePoll() {

    clearPoll();


    pollTimer =
        window.setTimeout(
            loadCurrentJob,
            2000
        );
}


function clearPoll() {

    if (
        pollTimer
        !== null
    ) {

        window.clearTimeout(
            pollTimer
        );


        pollTimer =
            null;
    }
}


/* ===================================================== */
/* QUEUE */
/* ===================================================== */

function queueStatusLabel(
    status
) {

    switch (
        status
    ) {

        case "queued":
            return "Queued";

        case "downloading":
            return "Downloading";

        case "processing":
            return "Processing";

        case "completed":
            return "Completed";

        case "failed":
            return "Failed";

        case "expired":
            return "Expired";

        default:
            return status;
    }
}


function renderQueue(
    data
) {

    queueActive.textContent =
        data.active;


    queueWaiting.textContent =
        data.queued;


    queueTotal.textContent =
        data.total;


    currentQueuePage =
        data.page;


    queueList.innerHTML =
        "";


    data.jobs.forEach(
        (
            job
        ) => {

            const item =
                document.createElement(
                    "article"
                );


            item.className =
                "queue-item";


            const isActive =
                (
                    job.status
                    === "downloading"

                    ||

                    job.status
                    === "processing"
                );


            if (
                isActive
            ) {

                item.classList.add(
                    "active-job"
                );
            }


            const identity =
                document.createElement(
                    "div"
                );


            const code =
                document.createElement(
                    "a"
                );


            code.className =
                "queue-code";


            code.href =
                `/${job.job_code}?from=queue&page=${currentQueuePage}`;


            code.textContent =
                job.job_code;


            const meta =
                document.createElement(
                    "div"
                );


            meta.className =
                "queue-meta";


            const qualitySuffix =
                job.media_type
                    === "video"
                    ? "p"
                    : "K";


            meta.textContent =
                `${job.media_type} · ${job.quality}${qualitySuffix} · ${formatCreatedAt(job.created_at)}`;


            identity.append(
                code,
                meta
            );


            const statusBlock =
                document.createElement(
                    "div"
                );


            const status =
                document.createElement(
                    "div"
                );


            status.className =
                "queue-status";


            status.textContent =
                queueStatusLabel(
                    job.status
                );


            statusBlock.appendChild(
                status
            );


            if (
                job.status
                    === "queued"

                &&

                job.queue_position
                    !== null
            ) {

                const position =
                    document.createElement(
                        "div"
                    );


                position.className =
                    "queue-position";


                position.textContent =
                    `Position #${job.queue_position}`;


                statusBlock.appendChild(
                    position
                );
            }


            const progress =
                document.createElement(
                    "div"
                );


            progress.className =
                "queue-progress";


            const total =
                Number(
                    job.total_items
                );


            const completed =
                Number(
                    job.completed_items
                );


            if (
                total > 0
            ) {

                progress.textContent =
                    `${completed} / ${total}`;


                const mini =
                    document.createElement(
                        "div"
                    );


                mini.className =
                    "queue-mini-progress";


                const fill =
                    document.createElement(
                        "div"
                    );


                const percentage =
                    Math.min(
                        100,

                        Math.max(
                            0,

                            completed
                            / total
                            * 100
                        )
                    );


                fill.style.width =
                    `${percentage}%`;


                mini.appendChild(
                    fill
                );


                progress.appendChild(
                    mini
                );


            } else {

                progress.textContent =
                    "—";
            }


            item.append(
                identity,
                statusBlock,
                progress
            );


            queueList.appendChild(
                item
            );
        }
    );


    queuePageText.textContent =
        `Page ${data.page} / ${data.pages}`;


    queuePrevious.disabled =
        data.page <= 1;


    queueNext.disabled =
        data.page >= data.pages;


    if (
        data.pages > 1
    ) {

        queuePagination.classList.remove(
            "hidden"
        );

    } else {

        queuePagination.classList.add(
            "hidden"
        );
    }
}


async function loadQueue(
    page =
        currentQueuePage
) {

    if (
        !queueAccessWord && accessMode !== "queue"
    ) {

        window.location.href =
            "/";

        return;
    }


    try {

        const data =
            await apiRequest(
                `/api/queue?page=${page}`,
                {
                    headers: {
                        "X-Queue-Access":
                            queueAccessWord || ""
                    }
                }
            );


        renderQueue(
            data
        );


        scheduleQueueRefresh();


    } catch (
        error
    ) {

        if (
            error.status
                === 403
        ) {

            sessionStorage.removeItem(
                "box4one-queue-access"
            );


            queueAccessWord = null;
            accessMode = null;
            sessionStorage.removeItem("box4one-access-mode");


            window.location.href =
                "/";


            return;
        }


        scheduleQueueRefresh();
    }
}


function scheduleQueueRefresh() {

    clearQueueTimer();


    queueTimer =
        window.setTimeout(
            () => {

                loadQueue(
                    currentQueuePage
                );
            },
            3000
        );
}


function clearQueueTimer() {

    if (
        queueTimer
        !== null
    ) {

        window.clearTimeout(
            queueTimer
        );


        queueTimer =
            null;
    }
}


queuePrevious.addEventListener(
    "click",
    () => {

        if (
            currentQueuePage
            <= 1
        ) {

            return;
        }


        loadQueue(
            currentQueuePage
            - 1
        );
    }
);


queueNext.addEventListener(
    "click",
    () => {

        loadQueue(
            currentQueuePage
            + 1
        );
    }
);


/* ===================================================== */
/* VIEWS */
/* ===================================================== */

function hideAllViews() {

    homeView.classList.add(
        "hidden"
    );


    jobView.classList.add(
        "hidden"
    );


    queueView.classList.add(
        "hidden"
    );
}


function showHome() {

    hideAllViews();


    homeView.classList.remove(
        "hidden"
    );


    if (
        unlocked
    ) {

        inputShell.classList.add(
            "unlocked"
        );
    }


    window.setTimeout(
        () => {

            mainInput.focus();
        },
        50
    );
}


function showJob() {

    const navigation = document.getElementById("jobNavigationLink");
    const parameters = new URLSearchParams(window.location.search);
    if (accessMode === "queue") {
        const page = Number(parameters.get("page"));
        const returnPage = Number.isSafeInteger(page) && page > 0 ? page : 1;
        navigation.href = `/queue?page=${returnPage}`;
        navigation.title = "Back to queue";
        navigation.setAttribute("aria-label", "Back to queue");
        navigation.querySelector("path").setAttribute("d", "M9 5 3 11l6 6M3 11h10a7 7 0 0 1 7 7v2");
        navigation.querySelector("path").setAttribute("fill", "none");
        navigation.querySelector("path").setAttribute("stroke", "currentColor");
        navigation.querySelector("path").setAttribute("stroke-width", "2");
        navigation.querySelector("path").setAttribute("stroke-linecap", "round");
        navigation.querySelector("path").setAttribute("stroke-linejoin", "round");
    }

    hideAllViews();


    jobView.classList.remove(
        "hidden"
    );


    loadCurrentJob();


    expirationTimer =
        window.setInterval(
            updateExpiration,
            1000
        );
}


function showQueue() {

    hideAllViews();


    queueView.classList.remove(
        "hidden"
    );


    const page = Number(new URLSearchParams(window.location.search).get("page"));
    loadQueue(Number.isSafeInteger(page) && page > 0 ? page : 1);
}


/* ===================================================== */
/* INITIALIZE */
/* ===================================================== */

async function initialize() {
    if (isStandaloneApp()) {
        // Never persist or reuse access words in the installed app.
        sessionStorage.removeItem("box4one-download-access");
        sessionStorage.removeItem("box4one-queue-access");
        downloadAccessWord = null;
        queueAccessWord = null;
        unlocked = false;
        accessMode = null;
        try {
            const access = await apiRequest("/api/access");
            accessMode = access.mode;
            unlocked = true;
        } catch (error) {
            if (error.status !== 403) console.warn("Unable to restore access", error);
        }
    }
    document.getElementById("homeQueueLink").classList.toggle("hidden", accessMode !== "queue");
    document.querySelectorAll(".logout-button").forEach(button => {
        button.classList.toggle("hidden", accessMode !== "queue");
    });

    const jobCode =
        getJobCodeFromPath();


    if (
        jobCode
    ) {

        if (
            window.location.pathname
                .startsWith(
                    "/job/"
                )
        ) {

            window.history
                .replaceState(
                    {},
                    "",
                    `/${jobCode}`
                );
        }


        showJob();

        return;
    }


    if (
        isQueuePath()
    ) {

        showQueue();

        return;
    }


    showHome();
}


window.addEventListener(
    "beforeunload",
    () => {

        clearPoll();

        clearQueueTimer();


        if (
            expirationTimer
            !== null
        ) {

            window.clearInterval(
                expirationTimer
            );
        }
    }
);


initialize();


async function logoutAccess() {
    try {
        await apiRequest("/api/access", {method: "DELETE"});
        ["box4one-access-mode", "box4one-download-access", "box4one-queue-access", "box4one-unlocked"].forEach(key => sessionStorage.removeItem(key));
        window.location.href = "/";
    } catch {
        window.alert("Unable to sign out. Please try again.");
    }
}
document.querySelectorAll(".logout-button").forEach(button => button.addEventListener("click", logoutAccess));

document.getElementById("shareJobButton").addEventListener("click", async () => {
    const code = getJobCodeFromPath();
    if (!code) return;
    const url = new URL(`/${code}`, window.location.origin).href;
    try {
        if (navigator.share) {
            await navigator.share({title: "BOX4ONE", url});
        } else {
            await navigator.clipboard.writeText(url);
            const button = document.getElementById("shareJobButton");
            button.textContent = "Link copied";
            window.setTimeout(() => button.textContent = "Share", 1500);
        }
    } catch (error) {
        if (error.name !== "AbortError") window.prompt("Copy job link", url);
    }
});


function showDownloadNotice() {
    document.getElementById("downloadNoticeText").textContent = isIOSWebApp()
        ? "Открываем Safari…" : "Передача файла в iOS…";
    document.getElementById("downloadNoticeHelp").textContent = isIOSWebApp()
        ? "Подтвердите открытие Safari, если появится запрос. После сохранения вернитесь в BOX4ONE. Если Safari не открылся, передача не подтверждена."
        : "Дождитесь системного окна. В нём можно сохранить файл. Сайт не получает подтверждение сохранения.";
    document.getElementById("downloadNotice").classList.remove("hidden");
}

document.getElementById("dismissDownloadNotice").addEventListener("click", () => {
    document.getElementById("downloadNotice").classList.add("hidden");
});


let activeMediaPlayer = null;

function closePlayback() {
    if (activeMediaPlayer) {
        activeMediaPlayer.pause();
        activeMediaPlayer.removeAttribute("src");
        activeMediaPlayer.load();
        activeMediaPlayer = null;
    }
    document.getElementById("inlineMediaContainer").replaceChildren();
    document.getElementById("inlinePlayback").classList.add("hidden");
}

async function enterMediaFullscreen(player) {
    try {
        if (typeof player.webkitEnterFullscreen === "function") {
            player.webkitEnterFullscreen();
            return true;
        }
        if (typeof player.requestFullscreen === "function") {
            await player.requestFullscreen();
            return true;
        }
    } catch {
        // iOS may require another user gesture once metadata has loaded.
    }
    return false;
}

function playInsideApp(file) {
    if (!isPlaybackEnabled()) return;
    closePlayback();
    const panel = document.getElementById("inlinePlayback");
    const status = document.getElementById("inlinePlaybackStatus");
    const fullscreen = document.getElementById("fullscreenPlayback");
    const video = /\.mp4$/i.test(file.filename);
    const player = document.createElement(video ? "video" : "audio");
    activeMediaPlayer = player;
    document.getElementById("inlinePlaybackName").textContent = file.filename;
    panel.classList.remove("hidden");
    fullscreen.classList.toggle("hidden", !video);
    fullscreen.disabled = true;
    player.controls = true;
    player.preload = "metadata";
    player.setAttribute("playsinline", "");
    status.textContent = "Opening media…";
    let fullscreenOpened = false;
    let fullscreenPending = false;
    async function tryFullscreen() {
        if (!video || activeMediaPlayer !== player || fullscreenOpened || fullscreenPending) return;
        fullscreenPending = true;
        fullscreenOpened = await enterMediaFullscreen(player);
        fullscreenPending = false;
    }
    player.addEventListener("loadedmetadata", () => {
        if (activeMediaPlayer !== player) return;
        fullscreen.disabled = false;
        if (video) tryFullscreen();
    });
    player.addEventListener("canplay", () => {
        if (activeMediaPlayer !== player) return;
        status.textContent = "";
    });
    player.addEventListener("waiting", () => {
        if (activeMediaPlayer === player) status.textContent = "Buffering…";
    });
    player.addEventListener("error", () => {
        if (activeMediaPlayer === player) status.textContent = "Unable to play this file. It may have expired.";
    });
    const url = new URL(file.download_url, window.location.origin);
    url.searchParams.set("preview", "true");
    player.src = url.href;
    document.getElementById("inlineMediaContainer").appendChild(player);
    // Start playback during the original button gesture. Media stays in the app.
    player.play().catch(() => {
        if (activeMediaPlayer === player && !player.error) status.textContent = "Press Play in the player.";
    });
    if (video) tryFullscreen();
    panel.scrollIntoView({block: "nearest"});
}

document.getElementById("closePlayback").addEventListener("click", closePlayback);
document.getElementById("fullscreenPlayback").addEventListener("click", async () => {
    const player = activeMediaPlayer;
    if (player && !(await enterMediaFullscreen(player))) {
        document.getElementById("inlinePlaybackStatus").textContent = "Use the fullscreen control in the player.";
    }
});
window.addEventListener("pagehide", closePlayback);
