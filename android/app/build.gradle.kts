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
        versionCode = 1
        versionName = "0.1.0"
        ndk {
            // 32bitový i 64bitový Android (řada Android TV boxů má 32bitový systém)
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
            // jen pro zkoušku v emulátoru: -PextraAbi=x86
            providers.gradleProperty("extraAbi").orNull?.let { abiFilters += it.split(",") }
        }
    }
    signingConfigs {
        // Aktualizace jde nainstalovat přes starší verzi jen se stejným podpisem.
        // Klíč se předává zvenčí: -PsigningKeystore=/cesta/k/souboru.keystore
        val keystore = providers.gradleProperty("signingKeystore").orNull
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
        buildPython("/usr/bin/python3.11")
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
    implementation("androidx.media3:media3-ui:1.8.0")
    implementation("com.github.bumptech.glide:glide:4.16.0")
}
