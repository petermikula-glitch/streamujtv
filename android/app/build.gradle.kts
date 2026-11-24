plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// Logika aplikace je sdílená s doplňkem pro Kodi: tyto moduly na Kodi nezávisí
// a kopírují se do aplikace při každém sestavení (jeden zdroj pravdy, stejné testy).
val pluginLib = rootProject.file("../plugin.video.prehrajto.mojevidea/resources/lib")
val pluginModules = listOf(
    "catalog.py", "csfd.py", "db.py", "ivysilani.py", "nameparser.py", "prehrajto.py", "provider.py",
    "search_results.py", "tmdb.py",
)
val pluginPython = layout.buildDirectory.dir("generated/plugin-python")
val syncPluginCore by tasks.registering(Copy::class) {
    from(pluginLib) { include(pluginModules) }
    into(pluginPython)
}

android {
    namespace = "cz.petermikula.mojevidea"
    compileSdk = 35

    defaultConfig {
        applicationId = "cz.petermikula.mojevidea"
        minSdk = 24
        targetSdk = 35
        versionCode = 35
        versionName = "0.28.0"
        ndk {
            // 32bitový i 64bitový Android (řada Android TV boxů má 32bitový systém)
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
            // jen pro zkoušku v emulátoru: -PextraAbi=x86
            providers.gradleProperty("extraAbi").orNull?.let { abiFilters += it.split(",") }
        }
    }
    signingConfigs {
        // Aktualizace jde nainstalovat přes starší verzi jen se stejným podpisem.
        // Klíč aplikace je v signing/mojevidea.keystore; jiný lze předat -PsigningKeystore=/cesta/k/souboru.
        val keystore = providers.gradleProperty("signingKeystore").orNull
            ?: rootProject.file("signing/mojevidea.keystore").takeIf { it.exists() }?.path
        if (keystore != null) {
            create("personal") {
                storeFile = file(keystore)
                storePassword = providers.gradleProperty("signingPassword").orElse("android").get()
                keyAlias = providers.gradleProperty("signingAlias").orElse("mojevidea").get()
                keyPassword = providers.gradleProperty("signingPassword").orElse("android").get()
            }
        }
    }
    buildTypes {
        debug {
            signingConfigs.findByName("personal")?.let { signingConfig = it }
            // Vydávané sestavení není laditelné: z laditelné aplikace jde přes ADB přečíst její data
            // (včetně uloženého hesla k Přehraj.to). Pro zkoušky v emulátoru: -Pdebuggable
            isDebuggable = providers.gradleProperty("debuggable").isPresent
        }
    }
    // Jen pro zkoušku v emulátoru za proxy, která znovu šifruje HTTPS: -PtestCa=/cesta/ca.crt
    // přidá do ladicího sestavení důvěru v danou autoritu. Do běžného sestavení se nedostane nic.
    providers.gradleProperty("testCa").orNull?.let { ca ->
        val dir = layout.buildDirectory.dir("generated/test-ca").get().asFile
        File(dir, "res/raw").mkdirs()
        File(dir, "res/xml").mkdirs()
        file(ca).copyTo(File(dir, "res/raw/test_ca.crt"), overwrite = true)
        File(dir, "res/xml/test_network_security.xml").writeText(
            "<network-security-config><base-config cleartextTrafficPermitted=\"true\"><trust-anchors>" +
                "<certificates src=\"system\"/><certificates src=\"@raw/test_ca\"/>" +
                "</trust-anchors></base-config></network-security-config>\n"
        )
        File(dir, "AndroidManifest.xml").writeText(
            "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">" +
                "<application android:networkSecurityConfig=\"@xml/test_network_security\"/></manifest>\n"
        )
        sourceSets.getByName("debug") {
            res.srcDir(File(dir, "res"))
            manifest.srcFile(File(dir, "AndroidManifest.xml"))
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        abortOnError = false
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        // Python pro sestavení: -PbuildPython=/cesta, jinak python3.11 ze systému (jinde ten na PATH)
        (providers.gradleProperty("buildPython").orNull
            ?: "/usr/bin/python3.11".takeIf { File(it).exists() })?.let { buildPython(it) }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(pluginPython)
        }
    }
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach {
    dependsOn(syncPluginCore)
}

dependencies {
    implementation("androidx.leanback:leanback:1.2.0")
    implementation("androidx.fragment:fragment-ktx:1.8.5")
    implementation("androidx.media3:media3-exoplayer:1.8.0")
    implementation("androidx.media3:media3-exoplayer-hls:1.8.0")
    implementation("androidx.media3:media3-exoplayer-dash:1.8.0")
    implementation("androidx.media3:media3-ui:1.8.0")
    // Softwarové dekodéry zvuku (DTS, TrueHD, AC-3…) pro soubory, které zařízení samo nepřehraje.
    // Verze musí odpovídat verzi Media3.
    implementation("org.jellyfin.media3:media3-ffmpeg-decoder:1.8.0+1")
    implementation("com.github.bumptech.glide:glide:4.16.0")
    // kód QR s hlášením z Diagnostiky (jen kodér, bez čtení a bez fotoaparátu)
    implementation("com.google.zxing:core:3.5.3")
    // řada „Pokračovat ve sledování“ na domovské obrazovce Android TV
    implementation("androidx.tvprovider:tvprovider:1.0.0")
}
