# GlanceSwitch pour Windows

**Le focus suit votre regard.** Regardez un autre écran ou une autre fenêtre,
tapez deux fois sur `Ctrl`, et le clavier, la souris et le curseur de texte y
passent directement. Pas de souris, pas d'`Alt+Tab`.

C'est une version Windows de l'idée de [glanceswitch.com](https://glanceswitch.com/)
(macOS), réécrite à partir de zéro.

## Fonctionnement

1. La webcam filme votre visage. [MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker)
   repère 478 points du visage, dont les iris, et calcule l'orientation de la tête.
2. Ces mesures (rotation et position de la tête, position des iris dans les yeux)
   sont converties en point sur le bureau grâce à une **calibration** propre à
   vous et à votre installation (régression ridge, filtre One Euro pour lisser).
3. Au déclenchement, GlanceSwitch classe les fenêtres selon la part de la
   zone d'incertitude du regard qui tombe sur leur partie visible. La meilleure,
   autre que la fenêtre active, passe au premier plan, et la souris y revient
   là où vous l'aviez laissée.

Tout se passe sur votre PC. Aucune image n'est enregistrée ni envoyée.

## Fonctionnalités

- **Multi-écrans** et **fenêtres d'un même écran**.
- Déclencheurs :
  - **Double `Ctrl`** (par défaut). Les raccourcis comme `Ctrl+C` ou `Ctrl+clic`
    ne déclenchent rien. Mauvaise fenêtre ? Refaites un double `Ctrl` dans les
    2,5 s, sans bouger le regard : GlanceSwitch passe à la suivante.
  - **Regard prolongé (dwell)** : fixer une fenêtre ~0,7 s suffit. Mis en pause
    pendant un glisser-déposer.
- Cible : **fenêtre regardée** ou **écran regardé** (la fenêtre du dessus de cet
  écran, plus tolérant aux imprécisions).
- La souris revient à sa dernière position dans chaque fenêtre.
- Bref contour bleu sur la fenêtre activée. Point de regard affichable pour le
  réglage.
- Icône dans la zone de notification, lancement au démarrage de Windows, choix
  de la caméra.
- Installation par utilisateur, **sans droits administrateur**.

## Installation

Téléchargez `GlanceSwitch-Setup-x.y.z.exe` depuis les
[Releases](../../releases), ou l'artefact du dernier build dans l'onglet
**Actions**. Une version portable (`.zip`) est aussi fournie.

L'installeur n'est pas signé, donc SmartScreen peut afficher un avertissement :
cliquez sur **Informations complémentaires**, puis **Exécuter quand même**.
Vérifiez l'empreinte avec `Get-FileHash` et `SHA256SUMS.txt`.

Configuration requise : Windows 10 ou 11 (64 bits) et une webcam.

## Premiers pas

1. Au premier lancement, la **calibration** démarre : fixez chaque point jusqu'à
   ce qu'il disparaisse (9 points par écran, environ 15 s par écran).
2. Regardez une autre fenêtre et tapez deux fois sur `Ctrl`.
3. Recalibrez (menu de l'icône → **Calibrer…**) si vous déplacez la caméra, les
   écrans ou votre chaise.

Conseils : caméra au-dessus ou sous l'écran principal, visage bien éclairé, pas
de contre-jour. Tourner légèrement la tête vers la fenêtre visée, plutôt que
seulement les yeux, améliore nettement la précision.

## Depuis les sources

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m glance_switch            # lance l'application
python -m pytest                   # tests
python packaging\prepare_build.py  # modèle + icône
pyinstaller packaging\GlanceSwitch.spec
```

Le build Windows complet (tests, exe, installeur Inno Setup, zip, SHA-256) tourne
dans GitHub Actions : `.github/workflows/build-windows.yml`. Poussez un tag
`v*` pour publier une release.

## Structure

| Fichier | Rôle |
| --- | --- |
| `glance_switch/tracker.py` | Webcam + MediaPipe dans un thread |
| `glance_switch/features.py` | Points du visage → vecteur de regard |
| `glance_switch/gaze_model.py` | Calibration : vecteur → pixels du bureau |
| `glance_switch/smoothing.py` | Filtre One Euro |
| `glance_switch/triggers.py` | Détection du double `Ctrl` et du dwell |
| `glance_switch/targeting.py` | Choix de l'écran et de la fenêtre |
| `glance_switch/win32.py` | API Win32 : écrans, fenêtres, focus, hook clavier |
| `glance_switch/overlay.py` | Écrans de calibration, point de regard, contour |
| `glance_switch/tray.py` | Icône et menu de la zone de notification |
| `glance_switch/app.py` | Assemblage de l'ensemble |

Réglages et calibration : `%APPDATA%\GlanceSwitch\` (`settings.json`,
`calibration.json`, `glance_switch.log`).

## Limites connues

- Une webcam donne une précision d'environ 2 à 4 cm à l'écran. Les grandes
  fenêtres et le mode multi-écrans marchent bien. Les petites fenêtres
  voisines sont moins fiables (utilisez alors le mode **Écran regardé**).
- Pas de bascule entre volets d'une même fenêtre (onglets, panneaux divisés).
- Les fenêtres lancées en administrateur ne peuvent pas recevoir le focus
  depuis un programme non-administrateur (limite de Windows, UIPI).
