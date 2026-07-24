using System.IO;
using UnityEditor;
using UnityEngine;

namespace OpenFaceFX.Editor
{
    /// <summary>Right-click any exported OpenFaceFX track JSON or ARKit Live Link CSV that's
    /// already in the project as a TextAsset → <b>OpenFaceFX ▸ Convert to OffX Clip</b>, and
    /// get an <see cref="OffxClip"/> asset next to it — no file renaming required.</summary>
    public static class OffxImportMenu
    {
        const string Path_ = "Assets/OpenFaceFX/Convert to OffX Clip";

        [MenuItem(Path_, true)]
        static bool Validate() => Selection.activeObject is TextAsset;

        [MenuItem(Path_, false, 30)]
        static void Convert()
        {
            var ta = Selection.activeObject as TextAsset;
            if (ta == null) return;
            string src = AssetDatabase.GetAssetPath(ta);
            OffxClip clip;
            try
            {
                clip = OffxParser.Parse(ta.text);
            }
            catch (System.Exception e)
            {
                EditorUtility.DisplayDialog("OpenFaceFX", "Parse failed:\n" + e.Message, "OK");
                return;
            }
            string dir = Path.GetDirectoryName(src);
            string outPath = AssetDatabase.GenerateUniqueAssetPath(
                Path.Combine(dir, Path.GetFileNameWithoutExtension(src) + ".asset"));
            AssetDatabase.CreateAsset(clip, outPath);
            AssetDatabase.SaveAssets();
            EditorGUIUtility.PingObject(clip);
            Debug.Log($"[OpenFaceFX] created {outPath} — {clip.channels.Count} channels, {clip.duration:0.00}s");
        }
    }
}
