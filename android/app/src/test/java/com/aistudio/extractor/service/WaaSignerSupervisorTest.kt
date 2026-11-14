package com.aistudio.extractor.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class WaaSignerSupervisorTest {
    @get:Rule
    val tempFolder = TemporaryFolder()

    @Test
    fun `formatDnsServers returns default fallbacks when system list is empty or blank`() {
        val result = WaaSignerSupervisor.formatDnsServers(listOf("", "   "))
        assertEquals("223.5.5.5:53,119.29.29.29:53,8.8.8.8:53,1.1.1.1:53", result)
    }

    @Test
    fun `formatDnsServers normalizes ipv4 ipv6 deduplicates and uses only system dns when non-empty`() {
        val result =
            WaaSignerSupervisor.formatDnsServers(
                listOf(
                    "192.168.1.1",
                    "8.8.8.8",
                    "8.8.8.8:53",
                    "2001:4860:4860::8888",
                    "fe80::1%wlan0",
                    "[fe80::1%wlan0]:53",
                    "10.0.0.1:5353",
                    "[2606:4700:4700::1111]:53",
                ),
            )
        assertEquals(
            "192.168.1.1:53,8.8.8.8:53,[2001:4860:4860::8888]:53," +
                "[fe80::1]:53,10.0.0.1:5353,[2606:4700:4700::1111]:53",
            result,
        )
    }

    @Test
    fun `formatDnsServers filters malformed entries zone ids and invalid ports`() {
        val filtered =
            WaaSignerSupervisor.formatDnsServers(
                listOf(
                    "http://8.8.8.8",
                    "https://1.1.1.1/dns-query",
                    "http:53",
                    "dns.google:53",
                    "8.8.8.8:0",
                    "8.8.8.8:99999",
                    "8.8.8.8:abc",
                    "192.168.1.1 8.8.8.8",
                    ":53",
                    "[fe80::1%wlan0]:0",
                    "[fe80::1%wlan0]:99999",
                    "[fe80::1%wlan0]:53",
                    "[fe80::2%eth0]",
                ),
            )
        assertEquals("[fe80::1]:53,[fe80::2]:53", filtered)

        val allInvalid =
            WaaSignerSupervisor.formatDnsServers(
                listOf("http://8.8.8.8", "http:53", "dns.google", "8.8.8.8:0", "8.8.8.8:99999"),
            )
        assertEquals("223.5.5.5:53,119.29.29.29:53,8.8.8.8:53,1.1.1.1:53", allInvalid)
    }

    @Test
    fun `buildEnvironment populates sandbox variables without proxy when proxy is blank`() {
        val env =
            WaaSignerSupervisor.buildEnvironment(
                tmpDirPath = "/data/user/0/com.aistudio.extractor/cache/waa_tmp",
                homeDirPath = "/data/user/0/com.aistudio.extractor/files",
                stateDirPath = "/data/user/0/com.aistudio.extractor/files/waa_workers",
                dnsServers = "8.8.8.8:53,1.1.1.1:53",
                proxyUrl = "   ",
            )

        assertEquals("/data/user/0/com.aistudio.extractor/cache/waa_tmp", env["TMPDIR"])
        assertEquals("/data/user/0/com.aistudio.extractor/cache/waa_tmp", env["TEMP"])
        assertEquals("/data/user/0/com.aistudio.extractor/cache/waa_tmp", env["TMP"])
        assertEquals("/data/user/0/com.aistudio.extractor/files", env["HOME"])
        assertEquals("/data/user/0/com.aistudio.extractor/files/waa_workers", env["WAA_STATE_DIR"])
        assertEquals(WaaSignerSupervisor.ANDROID_SSL_CERT_DIR, env["SSL_CERT_DIR"])
        assertEquals("8.8.8.8:53,1.1.1.1:53", env["WAA_DNS_SERVERS"])
        assertFalse(env.containsKey("AISTUDIO_PROXY"))
        assertFalse(env.containsKey("HTTPS_PROXY"))
        assertFalse(env.containsKey("HTTP_PROXY"))
    }

    @Test
    fun `buildEnvironment injects proxy variables when proxyUrl is provided`() {
        val env =
            WaaSignerSupervisor.buildEnvironment(
                tmpDirPath = "/tmp/waa",
                homeDirPath = "/home/waa",
                stateDirPath = "/state/waa",
                dnsServers = "8.8.8.8:53",
                proxyUrl = "  http://127.0.0.1:10809  ",
            )

        assertEquals("http://127.0.0.1:10809", env["AISTUDIO_PROXY"])
        assertEquals("http://127.0.0.1:10809", env["HTTPS_PROXY"])
        assertEquals("http://127.0.0.1:10809", env["HTTP_PROXY"])
    }

    @Test
    fun `buildCommand builds expected arguments without and with proxy`() {
        val baseCmd =
            WaaSignerSupervisor.buildCommand(
                binaryPath = "/data/app/lib/arm64/libwaa_signer.so",
                listenAddr = "127.0.0.1:8081",
                stateDirPath = "/data/files/waa_workers",
                maxWorkers = 2,
                idleTtl = "300s",
                dnsServers = "8.8.8.8:53,1.1.1.1:53",
                proxyUrl = null,
            )
        assertEquals(
            listOf(
                "/data/app/lib/arm64/libwaa_signer.so",
                "-listen",
                "127.0.0.1:8081",
                "-state-dir",
                "/data/files/waa_workers",
                "-max-workers",
                "2",
                "-idle-ttl",
                "300s",
                "-dns",
                "8.8.8.8:53,1.1.1.1:53",
            ),
            baseCmd,
        )

        val proxyCmd =
            WaaSignerSupervisor.buildCommand(
                binaryPath = "/data/app/lib/arm64/libwaa_signer.so",
                listenAddr = "127.0.0.1:8081",
                stateDirPath = "/data/files/waa_workers",
                maxWorkers = 2,
                idleTtl = "300s",
                dnsServers = "8.8.8.8:53",
                proxyUrl = "socks5://127.0.0.1:1080",
            )
        assertEquals(
            baseCmd.dropLast(1) + listOf("8.8.8.8:53", "-proxy", "socks5://127.0.0.1:1080"),
            proxyCmd,
        )
    }

    @Test
    fun `parseHealthJson updates status to RUNNING when ready is true`() {
        val current =
            SignerStatus(
                state = SignerRunState.STARTING,
                listenAddress = "127.0.0.1:8081",
                pid = 4321L,
                workers = 0,
                idleWorkers = 0,
                maxWorkers = 2,
                restartCount = 1,
                lastError = "previous transient error",
            )

        val snakeParsed =
            WaaSignerSupervisor.parseHealthJson(
                """{"ready":true,"workers":2,"idle_workers":1,"max_workers":4}""",
                current,
            )
        assertNotNull(snakeParsed)
        assertEquals(SignerRunState.RUNNING, snakeParsed!!.state)
        assertEquals(4321L, snakeParsed.pid)
        assertEquals(1, snakeParsed.restartCount)
        assertEquals(2, snakeParsed.workers)
        assertEquals(1, snakeParsed.idleWorkers)
        assertEquals(4, snakeParsed.maxWorkers)
        assertNull(snakeParsed.lastError)

        val camelParsed =
            WaaSignerSupervisor.parseHealthJson(
                """{"ready":true,"workers":1,"idleWorkers":1,"maxWorkers":2}""",
                current,
            )
        assertNotNull(camelParsed)
        assertEquals(SignerRunState.RUNNING, camelParsed!!.state)
        assertEquals(1, camelParsed.workers)
        assertEquals(1, camelParsed.idleWorkers)
        assertEquals(2, camelParsed.maxWorkers)
    }

    @Test
    fun `parseHealthJson returns null when not ready blank or invalid json`() {
        val current = SignerStatus(state = SignerRunState.STARTING)

        assertNull(WaaSignerSupervisor.parseHealthJson("", current))
        assertNull(WaaSignerSupervisor.parseHealthJson("   ", current))
        assertNull(WaaSignerSupervisor.parseHealthJson("""{"ready":false,"workers":0}""", current))
        assertNull(WaaSignerSupervisor.parseHealthJson("{invalid-json", current))
    }

    @Test
    fun `computeBackoffMs follows exponential backoff capped at 10s`() {
        assertEquals(1_000L, WaaSignerSupervisor.computeBackoffMs(1))
        assertEquals(2_000L, WaaSignerSupervisor.computeBackoffMs(2))
        assertEquals(4_000L, WaaSignerSupervisor.computeBackoffMs(3))
        assertEquals(8_000L, WaaSignerSupervisor.computeBackoffMs(4))
        assertEquals(10_000L, WaaSignerSupervisor.computeBackoffMs(5))
        assertEquals(10_000L, WaaSignerSupervisor.computeBackoffMs(10))
    }

    @Test
    fun `start enters ERROR state immediately when binary file does not exist`() {
        val missingBinary = File(tempFolder.root, "missing_libwaa_signer.so")
        val filesDir = tempFolder.newFolder("files")
        val cacheDir = tempFolder.newFolder("cache")
        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = missingBinary,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
            )

        try {
            supervisor.start()
            val reachedError = waitUntil { supervisor.status.value.state == SignerRunState.ERROR }
            assertTrue("Expected ERROR state when binary is missing", reachedError)
            assertNull(supervisor.status.value.pid)
            assertTrue(
                supervisor.status.value.lastError?.contains("not found") == true,
            )
        } finally {
            supervisor.stop()
        }
    }

    @Test
    fun `stop immediately after start during process spawn destroys started process and stays STOPPED`() {
        val binaryFile = tempFolder.newFile("libwaa_signer.so").apply { setExecutable(true) }
        val filesDir = tempFolder.newFolder("files_spawn")
        val cacheDir = tempFolder.newFolder("cache_spawn")
        val fakeProcess = FakeProcess()
        val spawnEntered = CountDownLatch(1)
        val allowSpawnReturn = CountDownLatch(1)

        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = binaryFile,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
                processStarter = { _ ->
                    spawnEntered.countDown()
                    allowSpawnReturn.await(3, TimeUnit.SECONDS)
                    fakeProcess
                },
                healthQueryOverride = { _, _ ->
                    SignerStatus(state = SignerRunState.RUNNING, workers = 1)
                },
            )

        try {
            supervisor.start()
            assertTrue("Process starter did not run in time", spawnEntered.await(3, TimeUnit.SECONDS))
            supervisor.stop()
            assertEquals(SignerRunState.STOPPED, supervisor.status.value.state)

            allowSpawnReturn.countDown()
            assertTrue(
                "Started process must be destroyed after stop during spawn",
                waitUntil { fakeProcess.destroyCalled.get() },
            )
            Thread.sleep(100)
            assertEquals(SignerRunState.STOPPED, supervisor.status.value.state)
            assertNull(supervisor.status.value.lastError)
        } finally {
            allowSpawnReturn.countDown()
            supervisor.stop()
        }
    }

    @Test
    fun `stop during stale health query prevents old generation from resurrecting status`() {
        val binaryFile = tempFolder.newFile("libwaa_signer_health.so").apply { setExecutable(true) }
        val filesDir = tempFolder.newFolder("files_health")
        val cacheDir = tempFolder.newFolder("cache_health")
        val fakeProcess = FakeProcess()
        val healthEntered = CountDownLatch(1)
        val allowHealthReturn = CountDownLatch(1)

        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = binaryFile,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
                processStarter = { _ -> fakeProcess },
                healthQueryOverride = { pid, restartCount ->
                    healthEntered.countDown()
                    allowHealthReturn.await(3, TimeUnit.SECONDS)
                    SignerStatus(
                        state = SignerRunState.RUNNING,
                        listenAddress = WaaSignerSupervisor.DEFAULT_LISTEN_ADDRESS,
                        pid = pid,
                        workers = 1,
                        idleWorkers = 1,
                        maxWorkers = 2,
                        restartCount = restartCount,
                    )
                },
            )

        try {
            supervisor.start()
            assertTrue("Health check did not start in time", healthEntered.await(3, TimeUnit.SECONDS))
            supervisor.stop()
            assertEquals(SignerRunState.STOPPED, supervisor.status.value.state)
            assertTrue(fakeProcess.destroyCalled.get())

            allowHealthReturn.countDown()
            Thread.sleep(150)
            assertEquals(SignerRunState.STOPPED, supervisor.status.value.state)
            assertNull(supervisor.status.value.lastError)
        } finally {
            allowHealthReturn.countDown()
            supervisor.stop()
        }
    }

    @Test
    fun `rapid stop then start isolates old generation from clobbering new generation`() {
        val binaryFile = tempFolder.newFile("libwaa_signer_gen.so").apply { setExecutable(true) }
        val filesDir = tempFolder.newFolder("files_gen")
        val cacheDir = tempFolder.newFolder("cache_gen")
        val gen1Process = FakeProcess()
        val gen2Process = FakeProcess()
        val spawnCount = AtomicInteger(0)
        val gen1HealthEntered = CountDownLatch(1)
        val allowGen1HealthReturn = CountDownLatch(1)

        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = binaryFile,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
                processStarter = { _ ->
                    if (spawnCount.incrementAndGet() == 1) gen1Process else gen2Process
                },
                healthQueryOverride = { pid, restartCount ->
                    if (spawnCount.get() == 1) {
                        gen1HealthEntered.countDown()
                        allowGen1HealthReturn.await(3, TimeUnit.SECONDS)
                        SignerStatus(
                            state = SignerRunState.ERROR,
                            pid = pid,
                            restartCount = restartCount,
                            lastError = "stale gen1 failure",
                        )
                    } else {
                        SignerStatus(
                            state = SignerRunState.RUNNING,
                            pid = pid,
                            workers = 2,
                            idleWorkers = 2,
                            maxWorkers = 2,
                            restartCount = restartCount,
                        )
                    }
                },
            )

        try {
            supervisor.start()
            assertTrue(gen1HealthEntered.await(3, TimeUnit.SECONDS))
            supervisor.stop()
            supervisor.start()

            assertTrue(
                "New generation should reach RUNNING",
                waitUntil { supervisor.status.value.state == SignerRunState.RUNNING },
            )
            allowGen1HealthReturn.countDown()
            Thread.sleep(150)

            assertTrue(gen1Process.destroyCalled.get())
            assertFalse(gen2Process.destroyCalled.get())
            assertEquals(SignerRunState.RUNNING, supervisor.status.value.state)
            assertNull(supervisor.status.value.lastError)
        } finally {
            allowGen1HealthReturn.countDown()
            supervisor.stop()
        }
    }

    @Test
    fun `startup timeout destroys spawned process and triggers restart`() {
        val binaryFile = tempFolder.newFile("libwaa_signer_timeout.so").apply { setExecutable(true) }
        val filesDir = tempFolder.newFolder("files_timeout")
        val cacheDir = tempFolder.newFolder("cache_timeout")
        val firstProcess = FakeProcess()
        val secondProcess = FakeProcess()
        val spawnCount = AtomicInteger(0)

        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = binaryFile,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
                processStarter = { _ ->
                    if (spawnCount.incrementAndGet() == 1) firstProcess else secondProcess
                },
                startupTimeoutMs = 50L,
                healthPollIntervalMs = 10L,
                initialBackoffMs = 10L,
                healthRequester = { _ -> null },
            )

        try {
            supervisor.start()
            assertTrue(
                "Expected a restart after the startup health check timed out",
                waitUntil { spawnCount.get() >= 2 },
            )
            assertTrue(
                "Timed-out process must be destroyed before retry",
                waitUntil { firstProcess.destroyCalled.get() },
            )
            assertTrue(
                "restartCount should reflect the retry",
                waitUntil { supervisor.status.value.restartCount >= 1 },
            )
        } finally {
            supervisor.stop()
        }
    }

    @Test
    fun `consecutive failures reaching maxRestartAttempts stop retrying and enter ERROR`() {
        val binaryFile = tempFolder.newFile("libwaa_signer_fail.so").apply { setExecutable(true) }
        val filesDir = tempFolder.newFolder("files_fail")
        val cacheDir = tempFolder.newFolder("cache_fail")
        val spawnCount = AtomicInteger(0)

        val supervisor =
            WaaSignerSupervisor(
                binaryFileOverride = binaryFile,
                filesDirOverride = filesDir,
                cacheDirOverride = cacheDir,
                systemDnsProvider = { listOf("192.168.1.1") },
                processStarter = { _ ->
                    FakeProcess().also { spawnCount.incrementAndGet() }
                },
                startupTimeoutMs = 50L,
                healthPollIntervalMs = 10L,
                initialBackoffMs = 10L,
                maxRestartAttempts = 2,
                healthRequester = { _ -> null },
            )

        try {
            supervisor.start()
            assertTrue(
                "Supervisor should reach the terminal ERROR state",
                waitUntil {
                    val status = supervisor.status.value
                    status.state == SignerRunState.ERROR &&
                        status.lastError?.contains("consecutive times") == true
                },
            )
            assertEquals(3, spawnCount.get())
            val status = supervisor.status.value
            assertEquals(SignerRunState.ERROR, status.state)
            assertEquals(2, status.restartCount)
            assertNull(status.pid)
        } finally {
            supervisor.stop()
        }
    }

    private fun waitUntil(
        timeoutMs: Long = 3_000L,
        condition: () -> Boolean,
    ): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) {
                return true
            }
            Thread.sleep(10)
        }
        return condition()
    }

    private class FakeProcess : Process() {
        val destroyCalled = AtomicBoolean(false)
        private val exitLatch = CountDownLatch(1)

        @Volatile
        private var exited = false

        override fun getOutputStream(): OutputStream = ByteArrayOutputStream()

        override fun getInputStream(): InputStream = ByteArrayInputStream(ByteArray(0))

        override fun getErrorStream(): InputStream = ByteArrayInputStream(ByteArray(0))

        override fun waitFor(): Int {
            exitLatch.await()
            return 0
        }

        override fun waitFor(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = exitLatch.await(timeout, unit)

        override fun exitValue(): Int {
            if (!exited) {
                throw IllegalThreadStateException("Process has not exited")
            }
            return 0
        }

        override fun isAlive(): Boolean = !exited

        override fun destroy() {
            destroyCalled.set(true)
            exited = true
            exitLatch.countDown()
        }

        override fun destroyForcibly(): Process {
            destroy()
            return this
        }
    }
}
