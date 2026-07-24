using System.IO;
using UnityEditor.AssetImporters;
using UnityEngine;

namespace OpenFaceFX.Editor
{
    /// <summary>Makes a <c>.offxtrack</c> file a first-class asset: import an OpenFaceFX
    /// track JSON (or an ARKit Live Link CSV) saved with that extension and it becomes an
    /// <see cref="OffxClip"/> you can drag onto an <see cref="OffxFacePlayer"/>.</summary>
    [ScriptedImporter(1, "offxtrack")]
    public class OffxTrackImporter : ScriptedImporter
    {
        [Tooltip("Frame rate to assume if the file is an ARKit CSV (ignored for track JSON).")]
        public float csvFps = OffxParser.DefaultCsvFps;

        public override void OnImportAsset(AssetImportContext ctx)
        {
            string text = File.ReadAllText(ctx.assetPath);
            OffxClip clip;
            try
            {
                clip = OffxParser.Parse(text, csvFps);
            }
            catch (System.Exception e)
            {
                ctx.LogImportError($"OpenFaceFX: could not parse '{ctx.assetPath}': {e.Message}");
                clip = ScriptableObject.CreateInstance<OffxClip>();
            }
            clip.name = Path.GetFileNameWithoutExtension(ctx.assetPath);
            ctx.AddObjectToAsset("clip", clip);
            ctx.SetMainObject(clip);
        }
    }
}
