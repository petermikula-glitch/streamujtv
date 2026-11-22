package cz.petermikula.mojevidea

import android.content.Context
import android.os.Environment
import android.os.storage.StorageManager
import java.io.File

/**
 * Úložiště, kam aplikace smí bez zvláštních oprávnění zapisovat: vlastní složka na každém připojeném
 * disku (`Android/data/<aplikace>/files`). Android ji při odinstalaci aplikace smaže.
 */
object Storage {
    class Volume(val dir: File, val label: String, val removable: Boolean) {
        val free: Long get() = dir.usableSpace
        val description: String get() = "$label · volno ${sizeText(free)}"
    }

    fun volumes(context: Context): List<Volume> {
        val manager = context.getSystemService(Context.STORAGE_SERVICE) as? StorageManager
        return context.getExternalFilesDirs(null).filterNotNull().mapNotNull { dir ->
            try {
                if (Environment.getExternalStorageState(dir) != Environment.MEDIA_MOUNTED) return@mapNotNull null
                if (!dir.exists() && !dir.mkdirs()) return@mapNotNull null
                val removable = Environment.isExternalStorageRemovable(dir)
                val name = manager?.getStorageVolume(dir)?.getDescription(context)
                Volume(dir, if (removable) name ?: "USB disk" else "Vnitřní úložiště", removable)
            } catch (error: RuntimeException) {
                null  // disk právě mizí nebo ho systém neumí popsat
            }
        }
    }

    /** Zvolené úložiště, je-li připojené; jinak první vyměnitelný disk. */
    fun target(context: Context, preferred: String): Volume? {
        val all = volumes(context)
        return all.firstOrNull { it.dir.absolutePath == preferred } ?: all.firstOrNull { it.removable }
    }

    /** FAT32 neuloží soubor větší než 4 GB; pozná se podle připojení disku v systému. */
    fun isFat(dir: File): Boolean {
        val id = dir.absolutePath.removePrefix("/storage/").substringBefore('/')
        if (id.isEmpty() || id == "emulated") return false
        return try {
            File("/proc/mounts").readLines().any { line ->
                val fields = line.split(' ')
                fields.size > 2 && fields[1].endsWith("/$id") && fields[2] in setOf("vfat", "msdos")
            }
        } catch (error: Exception) {
            false
        }
    }

    fun sizeText(bytes: Long): String = when {
        bytes >= 1L shl 30 -> String.format(java.util.Locale.ROOT, "%.1f GB", bytes / (1L shl 30).toDouble()).replace('.', ',')
        else -> "${bytes / (1L shl 20)} MB"
    }
}
