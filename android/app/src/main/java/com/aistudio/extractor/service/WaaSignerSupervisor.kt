package com.aistudio.extractor.service

import android.content.Context
import android.net.ConnectivityManager
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.TimeUnit

enum class SignerRunState {
    STOPPED,
    STARTING,
    RUNNING,
    ERROR,
}

data class SignerStatus(
    val state: SignerRunState = SignerRunState.STOPPED,
    val listenAddress: String = WaaSignerSupervisor.DEFAULT_LISTEN_ADDRESS,
    val pid: Long? = null,
    val workers: Int = 0,
    val idleWorkers: Int = 0,
    val maxWorkers: Int = WaaSignerSupervisor.DEFAULT_MAX_WORKERS,
    val restartCount: Int = 0,
    val lastError: String? = null,
)

/**
 * libwaa_signer.so 本地子进程生命周期守护器
 */
class WaaSignerSupervisor private constructor(
    private val context: Context?,
    private val listenAddress: String,
    private val maxWorkers: Int,
    private val idleTtl: String,
    private val onLog: ((String) -> Unit)?,
    private val binaryFileOverride: File?,
    private val filesDirOverride: File?,
    private val cacheDirOverride: File?,
    private val systemDnsProvider: (() -> List<String>)?,
    private val processStarter: (ProcessBuilder) -> Process = { it.start() },
    private val healthQueryOverride: ((Long?, Int) -> SignerStatus?)?,
    private val startupTimeoutMs: Long = DEFAULT_STARTUP_TIMEOUT_MS,
    private val healthPollIntervalMs: Long = HEALTH_POLL_INTERVAL_MS,
    private val initialBackoffMs: Long = INITIAL_BACKOFF_MS,
    private val maxRestartAttempts: Int = MAX_RESTART_ATTEMPTS,
    private val healthRequester: (String) -> Pair<Int, String>? = { url -> httpRequestHealth(url) },
) {
    constructor(
        context: Context,
        listenAddress: String = DEFAULT_LISTEN_ADDRESS,
        maxWorkers: Int = DEFAULT_MAX_WORKERS,
        idleTtl: String = DEFAULT_IDLE_TTL,
        onLog: ((String) -> Unit)? = null,
    ) : this(
        context = context,
        listenAddress = listenAddress,
        maxWorkers = maxWorkers,
        idleTtl = idleTtl,
        onLog = onLog,
        binaryFileOverride = null,
        filesDirOverride = null,
        cacheDirOverride = null,
        systemDnsProvider = null,
        processStarter = { it.start() },
        healthQueryOverride = null,
    )

    internal constructor(
        binaryFileOverride: File? = null,
        filesDirOverride: File? = null,
        cacheDirOverride: File? = null,
        listenAddress: String = DEFAULT_LISTEN_ADDRESS,
        maxWorkers: Int = DEFAULT_MAX_WORKERS,
        idleTtl: String = DEFAULT_IDLE_TTL,
        onLog: ((String) -> Unit)? = null,
        systemDnsProvider: (() -> List<String>)? = null,
        processStarter: ((ProcessBuilder) -> Process)? = null,
        healthQueryOverride: ((Long?, Int) -> SignerStatus?)? = null,
        startupTimeoutMs: Long = DEFAULT_STARTUP_TIMEOUT_MS,
        healthPollIntervalMs: Long = HEALTH_POLL_INTERVAL_MS,
        initialBackoffMs: Long = INITIAL_BACKOFF_MS,
        maxRestartAttempts: Int = MAX_RESTART_ATTEMPTS,
        healthRequester: ((String) -> Pair<Int, String>?)? = null,
    ) : this(
        context = null,
        listenAddress = listenAddress,
        maxWorkers = maxWorkers,
        idleTtl = idleTtl,
        onLog = onLog,
        binaryFileOverride = binaryFileOverride,
        filesDirOverride = filesDirOverride,
        cacheDirOverride = cacheDirOverride,
        systemDnsProvider = systemDnsProvider,
        processStarter = processStarter ?: { it.start() },
        healthQueryOverride = healthQueryOverride,
        startupTimeoutMs = startupTimeoutMs,
        healthPollIntervalMs = healthPollIntervalMs,
        initialBackoffMs = initialBackoffMs,
        maxRestartAttempts = maxRestartAttempts,
        healthRequester = healthRequester ?: { url -> httpRequestHealth(url) },
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lifecycleLock = Any()

    private val _status =
        MutableStateFlow(
            SignerStatus(
                state = SignerRunState.STOPPED,
                listenAddress = listenAddress,
                maxWorkers = maxWorkers,
            ),
        )
    val status: StateFlow<SignerStatus> = _status.asStateFlow()

    @Volatile
    private var shouldRun = false
    private var runGeneration: Long = 0L
    private var supervisorJob: Job? = null
    private var currentProcess: Process? = null

    fun start(proxyUrl: String? = null) {
        synchronized(lifecycleLock) {
            if (shouldRun && supervisorJob?.isActive == true) {
                return
            }
            runGeneration += 1L
            val generation = runGeneration
            shouldRun = true
            _status.value =
                SignerStatus(
                    state = SignerRunState.STARTING,
                    listenAddress = listenAddress,
                    maxWorkers = maxWorkers,
                    restartCount = 0,
                )
            supervisorJob =
                scope.launch {
                    runSupervisorLoop(generation, proxyUrl)
                }
        }
    }

    fun stop() {
        val jobToCancel: Job?
        val processToStop: Process?
        synchronized(lifecycleLock) {
            runGeneration += 1L
            shouldRun = false
            jobToCancel = supervisorJob
            supervisorJob = null
            processToStop = currentProcess
            currentProcess = null
            _status.value =
                SignerStatus(
                    state = SignerRunState.STOPPED,
                    listenAddress = listenAddress,
                    maxWorkers = maxWorkers,
                )
        }

        jobToCancel?.cancel()
        processToStop?.let { destroyProcess(it) }
    }

    private fun isCurrentGeneration(generation: Long): Boolean =
        synchronized(lifecycleLock) {
            generation == runGeneration && shouldRun
        }

    private fun updateStatusIfCurrent(
        generation: Long,
        transform: (SignerStatus) -> SignerStatus,
    ): Boolean =
        synchronized(lifecycleLock) {
            if (generation != runGeneration || !shouldRun) {
                return@synchronized false
            }
            _status.update(transform)
            true
        }

    private fun failAndStopIfCurrent(
        generation: Long,
        lastError: String,
        restartCount: Int,
    ) {
        synchronized(lifecycleLock) {
            if (generation != runGeneration || !shouldRun) {
                return
            }
            shouldRun = false
            supervisorJob = null
            _status.value =
                SignerStatus(
                    state = SignerRunState.ERROR,
                    listenAddress = listenAddress,
                    pid = null,
                    maxWorkers = maxWorkers,
                    restartCount = restartCount,
                    lastError = lastError,
                )
        }
    }

    private suspend fun runSupervisorLoop(
        generation: Long,
        proxyUrl: String?,
    ) {
        var consecutiveFailures = 0
        var restartCount = 0

        while (isCurrentGeneration(generation) && currentCoroutineContext().isActive) {
            val binaryFile = resolveBinaryFile()
            if (!binaryFile.exists() || !binaryFile.isFile) {
                val msg = "waa-signer binary not found: ${binaryFile.absolutePath}"
                logError(msg)
                failAndStopIfCurrent(generation, msg, restartCount)
                break
            }
            if (!binaryFile.canRead()) {
                val msg = "waa-signer binary not readable: ${binaryFile.absolutePath}"
                logError(msg)
                failAndStopIfCurrent(generation, msg, restartCount)
                break
            }
            if (!binaryFile.canExecute()) {
                val granted = binaryFile.setExecutable(true)
                if (!granted) {
                    logWarn("setExecutable(true) returned false for ${binaryFile.absolutePath}")
                }
            }

            val filesDir = resolveFilesDir()
            val cacheDir = resolveCacheDir()
            val stateDir = File(filesDir, "waa_workers").apply { mkdirs() }
            val tmpDir = File(cacheDir, "waa_tmp").apply { mkdirs() }
            val dnsServers = formatDnsServers(readSystemDnsIps())
            val env =
                buildEnvironment(
                    tmpDirPath = tmpDir.absolutePath,
                    homeDirPath = filesDir.absolutePath,
                    stateDirPath = stateDir.absolutePath,
                    dnsServers = dnsServers,
                    proxyUrl = proxyUrl,
                )
            val command =
                buildCommand(
                    binaryPath = binaryFile.absolutePath,
                    listenAddr = listenAddress,
                    stateDirPath = stateDir.absolutePath,
                    maxWorkers = maxWorkers,
                    idleTtl = idleTtl,
                    dnsServers = dnsServers,
                    proxyUrl = proxyUrl,
                )

            val updatedStarting =
                updateStatusIfCurrent(generation) {
                    it.copy(
                        state = SignerRunState.STARTING,
                        pid = null,
                        restartCount = restartCount,
                        lastError = null,
                    )
                }
            if (!updatedStarting) {
                break
            }

            var process: Process? = null
            var logJob: Job? = null
            var failureReason: String? = null

            try {
                if (!isCurrentGeneration(generation)) {
                    break
                }
                // Spawn outside the lifecycle lock so stop() is never blocked by process creation.
                val startedProcess =
                    withContext(NonCancellable) {
                        startProcess(command, stateDir, env)
                    }
                val adopted =
                    synchronized(lifecycleLock) {
                        if (generation != runGeneration || !shouldRun) {
                            false
                        } else {
                            process = startedProcess
                            currentProcess = startedProcess
                            true
                        }
                    }
                if (!adopted) {
                    destroyProcess(startedProcess)
                    break
                }

                val pid = extractPid(startedProcess)
                if (!updateStatusIfCurrent(generation) { it.copy(pid = pid) }) {
                    break
                }

                logJob =
                    scope.launch {
                        consumeProcessLogs(startedProcess)
                    }

                val ready = waitUntilHealthyAndMonitor(generation, startedProcess, pid, restartCount)
                if (ready) {
                    consecutiveFailures = 0
                } else if (isCurrentGeneration(generation) && startedProcess.isAlive) {
                    failureReason = "waa-signer /health check timed out after ${startupTimeoutMs}ms"
                    logWarn(failureReason)
                    destroyProcess(startedProcess)
                }

                val exitCode = startedProcess.waitFor()
                withTimeoutOrNull(LOG_DRAIN_TIMEOUT_MS) {
                    logJob.join()
                }
                if (failureReason == null) {
                    failureReason = "waa-signer exited with code $exitCode"
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                failureReason = e.message ?: e::class.java.simpleName
                logError("waa-signer supervisor error: $failureReason", e)
            } finally {
                logJob?.cancel()
                synchronized(lifecycleLock) {
                    if (currentProcess === process) {
                        currentProcess = null
                    }
                }
                process?.let { started ->
                    if (started.isAlive) {
                        destroyProcess(started)
                    }
                }
            }

            if (!isCurrentGeneration(generation) || !currentCoroutineContext().isActive) {
                break
            }

            consecutiveFailures++
            if (consecutiveFailures > maxRestartAttempts) {
                val errorMsg =
                    "waa-signer failed $consecutiveFailures consecutive times" +
                        (failureReason?.let { ": $it" } ?: "")
                logError(errorMsg)
                failAndStopIfCurrent(generation, errorMsg, restartCount)
                break
            }

            restartCount++
            val backoffMs = computeBackoffMs(consecutiveFailures, initialBackoffMs)
            val retryMsg =
                (failureReason ?: "waa-signer exited unexpectedly") +
                    ", restarting in ${backoffMs}ms (attempt $consecutiveFailures/$maxRestartAttempts)"
            logWarn(retryMsg)
            val updatedRetry =
                updateStatusIfCurrent(generation) {
                    it.copy(
                        state = SignerRunState.ERROR,
                        pid = null,
                        restartCount = restartCount,
                        lastError = retryMsg,
                    )
                }
            if (!updatedRetry) {
                break
            }
            delay(backoffMs)
        }
    }

    private suspend fun waitUntilHealthyAndMonitor(
        generation: Long,
        process: Process,
        pid: Long?,
        restartCount: Int,
    ): Boolean {
        val deadline = System.currentTimeMillis() + startupTimeoutMs
        while (isCurrentGeneration(generation) && currentCoroutineContext().isActive && process.isAlive) {
            val parsed = queryAndParseHealth(pid, restartCount)
            if (parsed != null) {
                return updateStatusIfCurrent(generation) { parsed }
            }
            if (System.currentTimeMillis() >= deadline) {
                return false
            }
            delay(healthPollIntervalMs)
        }
        return false
    }

    private suspend fun queryAndParseHealth(
        pid: Long?,
        restartCount: Int,
    ): SignerStatus? {
        healthQueryOverride?.let { override ->
            return override(pid, restartCount)
        }
        return withContext(Dispatchers.IO) {
            val response = healthRequester("http://$listenAddress/health") ?: return@withContext null
            if (response.first != HttpURLConnection.HTTP_OK) {
                return@withContext null
            }
            val base =
                SignerStatus(
                    listenAddress = listenAddress,
                    pid = pid,
                    maxWorkers = maxWorkers,
                    restartCount = restartCount,
                )
            parseHealthJson(response.second, base)
        }
    }

    private fun resolveBinaryFile(): File =
        binaryFileOverride ?: File(requireNotNull(context).applicationInfo.nativeLibraryDir, BINARY_NAME)

    private fun resolveFilesDir(): File = filesDirOverride ?: requireNotNull(context).filesDir

    private fun resolveCacheDir(): File = cacheDirOverride ?: requireNotNull(context).cacheDir

    private fun startProcess(
        command: List<String>,
        workingDir: File,
        env: Map<String, String>,
    ): Process {
        val pb =
            ProcessBuilder(command).apply {
                directory(workingDir)
                redirectErrorStream(true)
                environment().putAll(env)
            }
        return processStarter(pb)
    }

    private fun consumeProcessLogs(process: Process) {
        try {
            process.inputStream.bufferedReader().useLines { lines ->
                lines.forEach { line ->
                    logInfo(line)
                    onLog?.invoke(line)
                }
            }
        } catch (_: Exception) {
            // 进程退出或流关闭时正常结束
        }
    }

    private fun readSystemDnsIps(): List<String> {
        systemDnsProvider?.let { provider ->
            return try {
                provider()
            } catch (_: Exception) {
                emptyList()
            }
        }
        val ctx = context ?: return emptyList()
        return try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val network = cm?.activeNetwork ?: return emptyList()
            val linkProps = cm.getLinkProperties(network) ?: return emptyList()
            linkProps.dnsServers.mapNotNull { it.hostAddress }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun destroyProcess(process: Process) {
        try {
            process.destroy()
            val exited =
                try {
                    process.waitFor(STOP_GRACE_WAIT_MS, TimeUnit.MILLISECONDS)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    false
                }
            if (!exited && process.isAlive) {
                process.destroyForcibly()
            }
        } catch (e: Exception) {
            logWarn("Failed to destroy waa-signer process: ${e.message}")
        }
    }

    private fun logInfo(msg: String) {
        try {
            Log.i(TAG, msg)
        } catch (_: RuntimeException) {
            // 单元测试 JVM 环境下 android.util.Log 未 mock
        }
    }

    private fun logWarn(msg: String) {
        try {
            Log.w(TAG, msg)
        } catch (_: RuntimeException) {
            // 单元测试 JVM 环境下 android.util.Log 未 mock
        }
    }

    private fun logError(
        msg: String,
        tr: Throwable? = null,
    ) {
        try {
            if (tr != null) {
                Log.e(TAG, msg, tr)
            } else {
                Log.e(TAG, msg)
            }
        } catch (_: RuntimeException) {
            // 单元测试 JVM 环境下 android.util.Log 未 mock
        }
    }

    private fun extractPid(process: Process): Long? =
        try {
            val pidMethod = Process::class.java.getMethod("pid")
            (pidMethod.invoke(process) as? Number)?.toLong()
        } catch (_: Throwable) {
            try {
                val pidField = process.javaClass.getDeclaredField("pid")
                pidField.isAccessible = true
                (pidField.get(process) as? Number)?.toLong()
            } catch (_: Throwable) {
                null
            }
        }

    companion object {
        const val TAG = "WaaSigner"
        const val BINARY_NAME = "libwaa_signer.so"
        const val DEFAULT_LISTEN_ADDRESS = "127.0.0.1:8081"
        const val DEFAULT_MAX_WORKERS = 2
        const val DEFAULT_IDLE_TTL = "300s"
        const val DEFAULT_FALLBACK_DNS = "223.5.5.5:53,119.29.29.29:53,8.8.8.8:53,1.1.1.1:53"
        const val ANDROID_SSL_CERT_DIR =
            "/apex/com.android.conscrypt/cacerts:/system/etc/security/cacerts"

        private const val HEALTH_TIMEOUT_MS = 1500
        private const val DEFAULT_STARTUP_TIMEOUT_MS = 15_000L
        private const val HEALTH_POLL_INTERVAL_MS = 250L
        private const val STOP_GRACE_WAIT_MS = 3_000L
        private const val LOG_DRAIN_TIMEOUT_MS = 1_000L
        private const val INITIAL_BACKOFF_MS = 1_000L
        private const val MAX_RESTART_ATTEMPTS = 5
        private const val MAX_BACKOFF_MS = 10_000L

        internal fun httpRequestHealth(url: String): Pair<Int, String>? =
            try {
                val conn =
                    (URI.create(url).toURL().openConnection() as HttpURLConnection).apply {
                        requestMethod = "GET"
                        connectTimeout = HEALTH_TIMEOUT_MS
                        readTimeout = HEALTH_TIMEOUT_MS
                    }
                try {
                    val code = conn.responseCode
                    val body =
                        if (code == HttpURLConnection.HTTP_OK) {
                            conn.inputStream.bufferedReader().use { it.readText() }
                        } else {
                            ""
                        }
                    code to body
                } finally {
                    conn.disconnect()
                }
            } catch (_: Exception) {
                null
            }

        internal fun formatDnsServers(systemDnsIps: List<String>): String {
            val formatted = LinkedHashSet<String>()
            for (raw in systemDnsIps) {
                val ip = raw.trim()
                if (ip.isEmpty()) continue
                val normalized = normalizeDnsEndpoint(ip)
                if (!normalized.isNullOrEmpty()) {
                    formatted.add(normalized)
                }
            }
            if (formatted.isNotEmpty()) {
                return formatted.joinToString(",")
            }
            for (fallback in DEFAULT_FALLBACK_DNS.split(",")) {
                val trimmed = fallback.trim()
                if (trimmed.isNotEmpty()) {
                    formatted.add(trimmed)
                }
            }
            return formatted.joinToString(",")
        }

        private fun normalizeDnsEndpoint(raw: String): String? {
            val trimmed = raw.trim()
            if (trimmed.isEmpty() ||
                trimmed.contains('/') ||
                trimmed.any { it.isWhitespace() || it.isISOControl() }
            ) {
                return null
            }
            if (trimmed.startsWith("[")) {
                val closeIdx = trimmed.indexOf(']')
                if (closeIdx <= 1) {
                    return null
                }
                val host = trimmed.substring(1, closeIdx).substringBefore('%').trim()
                if (!isValidIpv6Literal(host)) {
                    return null
                }
                val rest = trimmed.substring(closeIdx + 1)
                return when {
                    rest.isEmpty() -> "[$host]:53"
                    rest.startsWith(":") -> {
                        val port = parseValidPort(rest.substring(1)) ?: return null
                        "[$host]:$port"
                    }
                    else -> null
                }
            }
            val colonCount = trimmed.count { it == ':' }
            return when {
                colonCount == 0 -> {
                    val host = trimmed.substringBefore('%').trim()
                    if (isValidIpv4Literal(host)) "$host:53" else null
                }
                colonCount == 1 -> {
                    val host = trimmed.substringBefore(':').substringBefore('%').trim()
                    val portStr = trimmed.substringAfter(':')
                    if (!isValidIpv4Literal(host)) {
                        return null
                    }
                    val port = parseValidPort(portStr) ?: return null
                    "$host:$port"
                }
                else -> {
                    val host = trimmed.substringBefore('%').trim()
                    if (isValidIpv6Literal(host)) "[$host]:53" else null
                }
            }
        }

        private fun isValidIpv4Literal(host: String): Boolean {
            val parts = host.split('.')
            if (parts.size != 4) {
                return false
            }
            return parts.all { part ->
                part.isNotEmpty() &&
                    part.length <= 3 &&
                    part.all { it.isDigit() } &&
                    part.toInt() in 0..255
            }
        }

        private fun isValidIpv6Literal(host: String): Boolean {
            if (!host.contains(':')) {
                return false
            }
            val allowed =
                host.all {
                    it.isDigit() || it.lowercaseChar() in 'a'..'f' || it == ':' || it == '.'
                }
            if (!allowed) {
                return false
            }
            val compressionIdx = host.indexOf("::")
            if (compressionIdx >= 0 && host.indexOf("::", compressionIdx + 2) >= 0) {
                return false
            }
            val ipv4Tail = host.substringAfterLast(':').takeIf { it.contains('.') }
            if (ipv4Tail != null && !isValidIpv4Literal(ipv4Tail)) {
                return false
            }
            val head = if (ipv4Tail != null) host.substringBeforeLast(':') else host
            val groups = head.split(':').filter { it.isNotEmpty() }
            val invalidGroup =
                groups.any { group ->
                    group.length > 4 ||
                        !group.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }
                }
            if (invalidGroup) {
                return false
            }
            val totalGroups = groups.size + if (ipv4Tail != null) 2 else 0
            return if (compressionIdx >= 0) totalGroups <= 7 else totalGroups == 8
        }

        private fun parseValidPort(portStr: String): Int? {
            if (portStr.isEmpty() || !portStr.all { it.isDigit() }) {
                return null
            }
            val port = portStr.toIntOrNull() ?: return null
            return if (port in 1..65535) port else null
        }

        internal fun buildEnvironment(
            tmpDirPath: String,
            homeDirPath: String,
            stateDirPath: String,
            dnsServers: String,
            proxyUrl: String? = null,
        ): Map<String, String> {
            val env =
                linkedMapOf(
                    "TMPDIR" to tmpDirPath,
                    "TEMP" to tmpDirPath,
                    "TMP" to tmpDirPath,
                    "HOME" to homeDirPath,
                    "WAA_STATE_DIR" to stateDirPath,
                    "SSL_CERT_DIR" to ANDROID_SSL_CERT_DIR,
                    "WAA_DNS_SERVERS" to dnsServers,
                )
            val trimmedProxy = proxyUrl?.trim()
            if (!trimmedProxy.isNullOrEmpty()) {
                env["AISTUDIO_PROXY"] = trimmedProxy
                env["HTTPS_PROXY"] = trimmedProxy
                env["HTTP_PROXY"] = trimmedProxy
            }
            return env
        }

        internal fun buildCommand(
            binaryPath: String,
            listenAddr: String,
            stateDirPath: String,
            maxWorkers: Int,
            idleTtl: String,
            dnsServers: String,
            proxyUrl: String? = null,
        ): List<String> {
            val cmd =
                mutableListOf(
                    binaryPath,
                    "-listen",
                    listenAddr,
                    "-state-dir",
                    stateDirPath,
                    "-max-workers",
                    maxWorkers.toString(),
                    "-idle-ttl",
                    idleTtl,
                    "-dns",
                    dnsServers,
                )
            val trimmedProxy = proxyUrl?.trim()
            if (!trimmedProxy.isNullOrEmpty()) {
                cmd.add("-proxy")
                cmd.add(trimmedProxy)
            }
            return cmd
        }

        internal fun parseHealthJson(
            raw: String,
            current: SignerStatus,
        ): SignerStatus? {
            if (raw.isBlank()) return null
            return try {
                val obj = JSONObject(raw)
                if (!obj.optBoolean("ready", false)) {
                    return null
                }
                val workers = obj.optInt("workers", current.workers)
                val idleWorkers =
                    when {
                        obj.has("idle_workers") -> obj.optInt("idle_workers", current.idleWorkers)
                        obj.has("idleWorkers") -> obj.optInt("idleWorkers", current.idleWorkers)
                        else -> current.idleWorkers
                    }
                val maxWorkers =
                    when {
                        obj.has("max_workers") -> obj.optInt("max_workers", current.maxWorkers)
                        obj.has("maxWorkers") -> obj.optInt("maxWorkers", current.maxWorkers)
                        else -> current.maxWorkers
                    }
                current.copy(
                    state = SignerRunState.RUNNING,
                    workers = workers,
                    idleWorkers = idleWorkers,
                    maxWorkers = maxWorkers,
                    lastError = null,
                )
            } catch (_: Exception) {
                null
            }
        }

        internal fun computeBackoffMs(
            consecutiveFailures: Int,
            initialBackoffMs: Long = INITIAL_BACKOFF_MS,
        ): Long {
            if (consecutiveFailures <= 1) return initialBackoffMs
            val shift = (consecutiveFailures - 1).coerceAtMost(10)
            return minOf(initialBackoffMs shl shift, MAX_BACKOFF_MS)
        }
    }
}
