using System;
using System.Collections.Generic;
using UnityEngine;

namespace OpenFaceFX
{
    /// <summary>Plays an <see cref="OffxClip"/> on a character at runtime: streams each
    /// channel's value onto the matching blendshape of a <see cref="SkinnedMeshRenderer"/>,
    /// and (optionally) drives head/eye bones from the pose channels.</summary>
    /// <remarks>Drop this on the avatar, assign the face renderer and a clip (or a track
    /// JSON / ARKit CSV <see cref="TextAsset"/>), press play. Channel names are matched to
    /// blendshape names case-insensitively, tolerating a mesh prefix (<c>Head.jawOpen</c>)
    /// and Live-Link PascalCase vs ARKit camelCase (<c>JawOpen</c> ⇄ <c>jawOpen</c>), so it
    /// works with Microsoft Rocketbox ARKit avatars and most ARKit-blendshape rigs.</remarks>
    [AddComponentMenu("OpenFaceFX/OffX Face Player")]
    public class OffxFacePlayer : MonoBehaviour
    {
        [Header("Source")]
        [Tooltip("A parsed clip asset (from the .offxtrack importer or CreateInstance).")]
        public OffxClip clip;
        [Tooltip("Optional: a track-JSON (.offxtrack) or ARKit-CSV file dropped in as a TextAsset. " +
                 "Used when Clip is empty; parsed on Awake.")]
        public TextAsset sourceText;
        [Tooltip("Frame rate to assume for an ARKit CSV source (its rows are a uniform grid). " +
                 "Ignored for track JSON, which carries explicit times.")]
        public float csvFps = OffxParser.DefaultCsvFps;

        [Header("Target")]
        [Tooltip("The face mesh whose blendshapes carry the ARKit shapes.")]
        public SkinnedMeshRenderer faceRenderer;
        [Tooltip("Optional prefix on this mesh's blendshape names, e.g. \"head_blendShapes.\".")]
        public string blendShapePrefix = "";
        [Range(0f, 100f)]
        [Tooltip("Full-activation weight. Unity blendshapes are 0..100; OpenFaceFX values are 0..1.")]
        public float weightScale = 100f;

        [Header("Playback")]
        public bool playOnAwake = true;
        public bool loop = true;
        [Min(0f)] public float speed = 1f;

        [Header("Head / eye pose (optional)")]
        [Tooltip("Drive these bones from the HeadYaw/Pitch/Roll and eye rotation channels (degrees).")]
        public bool applyHeadPose = false;
        public Transform headBone;
        public Transform leftEyeBone;
        public Transform rightEyeBone;
        [Tooltip("Scales the pose rotation (0 = off, 1 = as authored). Flip to invert an axis if a rig turns the wrong way.")]
        public float headPoseScale = 1f;

        /// <summary>Current playhead in seconds.</summary>
        public float Time01Seconds { get; private set; }
        public bool IsPlaying { get; private set; }
        public float Duration => clip != null ? clip.duration : 0f;

        // channel-name → resolved blendshape index (>=0), cached; -1 = no match.
        struct Bind { public OffxChannel ch; public int shape; }
        readonly List<Bind> _binds = new List<Bind>();
        OffxChannel _headYaw, _headPitch, _headRoll;
        OffxChannel _lEyeYaw, _lEyePitch, _lEyeRoll, _rEyeYaw, _rEyePitch, _rEyeRoll;
        Quaternion _headRest, _lEyeRest, _rEyeRest;
        bool _bound;

        static readonly HashSet<string> PoseChannels = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "HeadYaw","HeadPitch","HeadRoll",
            "LeftEyeYaw","LeftEyePitch","LeftEyeRoll",
            "RightEyeYaw","RightEyePitch","RightEyeRoll"
        };

        void Awake()
        {
            if (clip == null && sourceText != null)
            {
                try { clip = OffxParser.Parse(sourceText.text, csvFps); }
                catch (Exception e) { Debug.LogError($"[OpenFaceFX] failed to parse source '{sourceText.name}': {e.Message}", this); }
            }
            Bind();
            if (playOnAwake) Play();
        }

        /// <summary>Assign a clip at runtime and (re)bind to the current renderer.</summary>
        public void SetClip(OffxClip c) { clip = c; _bound = false; Bind(); Seek(0f); }

        /// <summary>Resolve every channel to a blendshape index / pose bone. Safe to call again
        /// after changing the clip or renderer.</summary>
        public void Bind()
        {
            _binds.Clear();
            _headYaw = _headPitch = _headRoll = null;
            _lEyeYaw = _lEyePitch = _lEyeRoll = _rEyeYaw = _rEyePitch = _rEyeRoll = null;
            _bound = false;
            if (clip == null) return;

            if (headBone != null) _headRest = headBone.localRotation;
            if (leftEyeBone != null) _lEyeRest = leftEyeBone.localRotation;
            if (rightEyeBone != null) _rEyeRest = rightEyeBone.localRotation;

            var index = BuildShapeIndex(faceRenderer);
            foreach (var ch in clip.channels)
            {
                if (ch == null || string.IsNullOrEmpty(ch.name)) continue;
                if (PoseChannels.Contains(ch.name)) { BindPose(ch); continue; }
                int shape = ResolveShape(index, ch.name);
                if (shape >= 0) _binds.Add(new Bind { ch = ch, shape = shape });
            }
            _bound = true;
        }

        void BindPose(OffxChannel ch)
        {
            switch (ch.name.ToLowerInvariant())
            {
                case "headyaw": _headYaw = ch; break;
                case "headpitch": _headPitch = ch; break;
                case "headroll": _headRoll = ch; break;
                case "lefteyeyaw": _lEyeYaw = ch; break;
                case "lefteyepitch": _lEyePitch = ch; break;
                case "lefteyeroll": _lEyeRoll = ch; break;
                case "righteyeyaw": _rEyeYaw = ch; break;
                case "righteyepitch": _rEyePitch = ch; break;
                case "righteyeroll": _rEyeRoll = ch; break;
            }
        }

        public void Play() { if (!_bound) Bind(); IsPlaying = clip != null; }
        public void Pause() { IsPlaying = false; }
        public void Stop() { IsPlaying = false; Seek(0f); }

        /// <summary>Jump to a time (seconds) and apply the pose there.</summary>
        public void Seek(float seconds)
        {
            Time01Seconds = (Duration > 0f) ? Mathf.Clamp(seconds, 0f, Duration) : 0f;
            ApplyAt(Time01Seconds);
        }

        void Update()
        {
            if (!IsPlaying || clip == null) return;
            float t = Time01Seconds + Time.deltaTime * Mathf.Max(0f, speed);
            if (Duration > 0f)
            {
                if (t >= Duration)
                {
                    if (loop) t %= Duration;
                    else { t = Duration; IsPlaying = false; }
                }
            }
            Time01Seconds = t;
            ApplyAt(t);
        }

        /// <summary>Sample the clip at <paramref name="t"/> and push weights/pose onto the target.
        /// Public so a timeline / external clock can drive it without this component's Update.</summary>
        public void ApplyAt(float t)
        {
            if (!_bound) Bind();
            if (faceRenderer != null)
            {
                for (int i = 0; i < _binds.Count; i++)
                {
                    var b = _binds[i];
                    float w = Mathf.Clamp01(b.ch.Sample(t)) * weightScale;
                    faceRenderer.SetBlendShapeWeight(b.shape, w);
                }
            }
            if (applyHeadPose && headPoseScale != 0f) ApplyPose(t);
        }

        void ApplyPose(float t)
        {
            if (headBone != null && (_headYaw != null || _headPitch != null || _headRoll != null))
            {
                float yaw = _headYaw != null ? _headYaw.Sample(t) : 0f;
                float pitch = _headPitch != null ? _headPitch.Sample(t) : 0f;
                float roll = _headRoll != null ? _headRoll.Sample(t) : 0f;
                headBone.localRotation = _headRest * Quaternion.Euler(pitch * headPoseScale, yaw * headPoseScale, roll * headPoseScale);
            }
            if (leftEyeBone != null && (_lEyeYaw != null || _lEyePitch != null))
            {
                float yaw = _lEyeYaw != null ? _lEyeYaw.Sample(t) : 0f;
                float pitch = _lEyePitch != null ? _lEyePitch.Sample(t) : 0f;
                float roll = _lEyeRoll != null ? _lEyeRoll.Sample(t) : 0f;
                leftEyeBone.localRotation = _lEyeRest * Quaternion.Euler(pitch * headPoseScale, yaw * headPoseScale, roll * headPoseScale);
            }
            if (rightEyeBone != null && (_rEyeYaw != null || _rEyePitch != null))
            {
                float yaw = _rEyeYaw != null ? _rEyeYaw.Sample(t) : 0f;
                float pitch = _rEyePitch != null ? _rEyePitch.Sample(t) : 0f;
                float roll = _rEyeRoll != null ? _rEyeRoll.Sample(t) : 0f;
                rightEyeBone.localRotation = _rEyeRest * Quaternion.Euler(pitch * headPoseScale, yaw * headPoseScale, roll * headPoseScale);
            }
        }

        // ---- blendshape name resolution ----------------------------------- //
        static Dictionary<string, int> BuildShapeIndex(SkinnedMeshRenderer smr)
        {
            var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            if (smr == null || smr.sharedMesh == null) return map;
            var mesh = smr.sharedMesh;
            for (int i = 0; i < mesh.blendShapeCount; i++)
            {
                string full = mesh.GetBlendShapeName(i);
                if (!map.ContainsKey(full)) map[full] = i;
                int dot = full.LastIndexOf('.');                 // "Head.jawOpen" → also index "jawOpen"
                if (dot >= 0 && dot + 1 < full.Length)
                {
                    string tail = full.Substring(dot + 1);
                    if (!map.ContainsKey(tail)) map[tail] = i;
                }
            }
            return map;
        }

        int ResolveShape(Dictionary<string, int> index, string channelName)
        {
            if (index.Count == 0) return -1;
            string[] candidates =
            {
                blendShapePrefix + channelName,
                channelName,
                LowerFirst(channelName),                         // JawOpen → jawOpen (ARKit camelCase)
                UpperFirst(channelName),                         // jawOpen → JawOpen (Live Link PascalCase)
                blendShapePrefix + LowerFirst(channelName),
            };
            foreach (var cand in candidates)
            {
                int idx;
                if (!string.IsNullOrEmpty(cand) && index.TryGetValue(cand, out idx)) return idx;
            }
            return -1;
        }

        static string LowerFirst(string s) =>
            string.IsNullOrEmpty(s) ? s : char.ToLowerInvariant(s[0]) + s.Substring(1);
        static string UpperFirst(string s) =>
            string.IsNullOrEmpty(s) ? s : char.ToUpperInvariant(s[0]) + s.Substring(1);

        /// <summary>How many channels resolved to a blendshape — useful in a custom editor
        /// or a quick <c>Debug.Log</c> to confirm the rig is wired up.</summary>
        public int BoundShapeCount { get { if (!_bound) Bind(); return _binds.Count; } }
    }
}
