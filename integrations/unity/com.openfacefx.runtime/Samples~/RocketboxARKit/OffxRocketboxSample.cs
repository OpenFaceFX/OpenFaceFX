using UnityEngine;

namespace OpenFaceFX.Samples
{
    /// <summary>Minimal driver for the Rocketbox ARKit sample: plays a clip on a face mesh
    /// and draws a tiny on-screen scrubber so you can scrape through the performance.
    /// Add it next to (or instead of) an <see cref="OffxFacePlayer"/>; if a player isn't
    /// assigned it adds one and wires the fields below.</summary>
    [AddComponentMenu("OpenFaceFX/Samples/Rocketbox Sample Driver")]
    public class OffxRocketboxSample : MonoBehaviour
    {
        public OffxFacePlayer player;
        public SkinnedMeshRenderer faceRenderer;
        public OffxClip clip;
        public TextAsset sourceText;        // fallback if 'clip' is empty (e.g. the ARKit CSV)

        void Start()
        {
            if (player == null) player = GetComponent<OffxFacePlayer>();
            if (player == null) player = gameObject.AddComponent<OffxFacePlayer>();
            if (faceRenderer != null) player.faceRenderer = faceRenderer;
            if (clip != null) player.clip = clip;
            else if (sourceText != null) player.sourceText = sourceText;
            player.loop = true;
            player.Play();
            Debug.Log($"[OpenFaceFX] Rocketbox sample — bound {player.BoundShapeCount} blendshapes, " +
                      $"{player.Duration:0.00}s");
        }

        void OnGUI()
        {
            if (player == null || player.Duration <= 0f) return;
            const int pad = 12, h = 24;
            var r = new Rect(pad, Screen.height - h - pad, Screen.width - 2 * pad, h);
            float t = GUI.HorizontalSlider(r, player.Time01Seconds, 0f, player.Duration);
            if (!Mathf.Approximately(t, player.Time01Seconds))
            {
                player.Pause();
                player.Seek(t);          // scrubbing pauses playback and jumps the head
            }
            GUI.Label(new Rect(pad, Screen.height - h - pad - 20, 300, 20),
                      $"OpenFaceFX  {player.Time01Seconds:0.00} / {player.Duration:0.00}s");
        }
    }
}
