#!/usr/bin/env python3
"""Reddit GTK - Card feed with inline images"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

import threading
import requests
import json
import re
import io
import webbrowser
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup

import subprocess

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

REDDIT_HOME = "https://old.reddit.com/r/popular/"
SORTS = ["best", "hot", "new", "top", "rising"]

IMAGE_EXT = re.compile(r'\.(jpe?g|png|webp)(\?.*)?$', re.I)
VIDEO_URL  = re.compile(r'(v\.redd\.it|youtu\.be|youtube\.com/watch|streamable\.com|gfycat\.com|redgifs\.com)', re.I)
JUNK_URL   = ["tracking","1x1","spacer","placeholder","favicon",
              "beacon","transparent","pixel.png","spinner","s.gif"]

def _open_mpv(url):
    try:
        subprocess.Popen(['mpv', '--profile=fast', '--vo=x11',
                          '--hwdec=no', url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        webbrowser.open(url)

CSS = b"""
* { font-family: sans-serif; }
window { background-color: #1e1e2e; }

/* Feed content centred with max width */
.feed-viewport {
    padding: 0 60px;
}

.card {
    background-color: #24273a;
    border-radius: 8px;
    padding: 14px;
    margin: 6px 0px;
}
.card:hover { background-color: #313244; }

/* Suppress all GTK light-mode button highlight / relief */
button {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
}
button:hover, button:active, button:focus {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    outline: none;
}

.title { font-size: 17px; font-weight: bold; color: #cdd6f4; }
.title:hover { color: #89b4fa; }
.meta { font-size: 14px; color: #a6adc8; }
.comment-text { font-size: 15px; color: #cdd6f4; }
.comment-author { font-size: 13px; font-weight: bold; color: #89b4fa; }
.comment-score { font-size: 13px; color: #fab387; }
.subreddit { font-size: 12px; font-weight: bold; color: #89b4fa; }
.score { font-size: 12px; color: #fab387; }
.comments-lbl { font-size: 12px; color: #a6adc8; }
.flair {
    font-size: 9px; color: #a6adc8;
    background-color: #45475a;
    border-radius: 4px; padding: 2px 6px;
}
.subbar {
    background-color: #181825;
    padding: 6px 16px;
    border-bottom: 1px solid #313244;
}
.sr-entry {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 11px;
    min-height: 0;
}
.sr-entry:focus {
    border-color: #89b4fa;
    border-width: 2px;
    box-shadow: 0 0 0 2px rgba(137,180,250,0.25);
    caret-color: #89b4fa;
}

.sr-label { color: #89b4fa; font-weight: bold; font-size: 12px; }
.sort-label {
    font-size: 11px;
    color: #6c7086;
    padding: 2px 8px;
    border-radius: 20px;
}
.sort-label-active {
    font-size: 11px;
    font-weight: bold;
    color: #89b4fa;
    padding: 2px 8px;
    border-radius: 20px;
}
.play-btn {
    font-size: 13px;
    color: #a6e3a1;
    padding: 2px 8px;
    border-radius: 6px;
    border: 1px solid #a6e3a1;
    background-color: transparent;
}
.play-btn:hover { background-color: #a6e3a1; color: #1e1e2e; }
.back-label {
    font-size: 13px;
    color: #6c7086;
    padding: 2px 6px;
}
.back-label:hover { color: #cdd6f4; }
.statusbar {
    background-color: #181825;
    color: #6c7086;
    font-size: 9px;
    padding: 3px 10px;
    border-top: 1px solid #313244;
}
.zoom-window {
    background-color: #000000;
}
/* EntryCompletion popup styling */
.entry-completion-popup,
GtkWindow.popup,
GtkWindow.popup GtkTreeView,
GtkWindow.popup GtkScrolledWindow {
    background-color: #24273a;
    color: #cdd6f4;
}
GtkWindow.popup GtkTreeView row {
    background-color: #24273a;
    color: #cdd6f4;
    padding: 3px 8px;
}
GtkWindow.popup GtkTreeView row:selected,
GtkWindow.popup GtkTreeView row:hover {
    background-color: #313244;
    color: #89b4fa;
}

"""

def _apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

def _clean(s):
    s = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', s or '')
    return re.sub(r'\s+', ' ', s).strip()

def _reltime(ts_str):
    try:
        dt    = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        delta = datetime.now(timezone.utc) - dt
        s     = delta.total_seconds()
        if s < 3600:      return f"{int(s//60)}m ago"
        if s < 86400:     return f"{int(s//3600)}h ago"
        if s < 86400*365: return f"{int(s//86400)}d ago"
        return f"{int(s//(86400*365))}y ago"
    except Exception:
        return ''

def _fmt_score(raw):
    try:
        n = int(re.sub(r'[^0-9\-]', '', (raw or '').split()[0]))
        return f"{n/1000:.1f}k" if abs(n) >= 1000 else str(n)
    except Exception:
        return raw or '•'

def _comment_count(thing):
    a = thing.select_one('a.comments')
    if not a: return '0'
    m = re.search(r'([\d,]+)\s*comment', a.get_text(), re.I)
    if m:
        n = int(m.group(1).replace(',', ''))
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)
    return '0'

def _best_preview(thing, page_url):
    # 1. Reddit's own preview data (best quality)
    raw_prev = thing.get('data-preview', '')
    if raw_prev:
        try:
            pdata = json.loads(raw_prev.replace('&lt;','<').replace('&gt;','>').replace('&amp;','&'))
            imgs = pdata.get('images', [])
            if imgs:
                resolutions = imgs[0].get('resolutions', [])
                source      = imgs[0].get('source', {})
                best = None
                for r in sorted(resolutions, key=lambda x: x.get('width', 0)):
                    if r.get('width', 0) <= 960:
                        best = r
                if best:
                    return best['url'].replace('&amp;', '&')
                if source:
                    return source['url'].replace('&amp;', '&')
        except Exception:
            pass

    data_url = thing.get('data-url', '').strip()

    # 2. Direct image URL
    if data_url and IMAGE_EXT.search(data_url):
        return urljoin(page_url, data_url)

    # 3. imgur single image
    if data_url:
        m = re.match(r'https?://(?:i\.)?imgur\.com/([a-zA-Z0-9]+)(?:\.\w+)?$', data_url)
        if m:
            return f"https://i.imgur.com/{m.group(1)}.jpg"

        # 4. imgur album/gallery
        m2 = re.match(r'https?://imgur\.com/(?:a|gallery)/([a-zA-Z0-9]+)', data_url)
        if m2:
            return f"https://i.imgur.com/{m2.group(1)}.jpg"

    # 5. i.redd.it direct
    if data_url and 'i.redd.it' in data_url:
        return data_url

    # 6. Reddit thumbnail as last resort
    thumb = thing.get('data-thumbnail-src', '') or thing.get('thumbnail-src', '')
    if not thumb:
        thumb_el = thing.select_one('a.thumbnail img')
        if thumb_el:
            thumb = thumb_el.get('src','')
    if thumb and thumb not in ('self','default','nsfw','spoiler','') \
             and not thumb.startswith('data:'):
        return urljoin(page_url, thumb)

    # 7. Fallback: img.preview tag
    preview = thing.select_one('img.preview')
    if preview:
        src = (preview.get('src') or '').strip()
        if src and not src.startswith('data:'):
            return urljoin(page_url, src)

    return None

def _fetch_pixbuf(url, session, max_w=200):
    try:
        lo = url.lower()
        if any(x in lo for x in JUNK_URL): return None
        r = session.get(url, timeout=10, headers={
            'User-Agent': UA,
            'Accept': 'image/webp,image/avif,image/apng,image/*,*/*;q=0.8'
        })
        r.raise_for_status()
        ct = r.headers.get('content-type', '')
        if 'image' not in ct: return None
        data = r.content
        if len(data) < 512: return None
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
        if not pb or pb.get_width() < 80 or pb.get_height() < 80:
            return None
        if pb.get_width() > max_w:
            ratio = max_w / pb.get_width()
            new_h = int(pb.get_height() * ratio)
            pb = pb.scale_simple(max_w, new_h, GdkPixbuf.InterpType.BILINEAR)
        return pb
    except Exception:
        return None

def _fetch_pixbuf_full(url, session):
    """Fetch full-size image for zoom view."""
    try:
        lo = url.lower()
        if any(x in lo for x in JUNK_URL): return None
        r = session.get(url, timeout=15, headers={
            'User-Agent': UA,
            'Accept': 'image/webp,image/avif,image/apng,image/*,*/*;q=0.8'
        })
        r.raise_for_status()
        ct = r.headers.get('content-type', '')
        if 'image' not in ct: return None
        data = r.content
        if len(data) < 512: return None
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
        return pb
    except Exception:
        return None

def _show_zoom_window(url, session):
    """Open a popup window showing the full-size image."""
    win = Gtk.Window()
    win.set_title("Image")
    win.set_default_size(800, 600)
    win.get_style_context().add_class('zoom-window')

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    img_widget = Gtk.Image()
    loading_lbl = Gtk.Label(label="Loading…")
    loading_lbl.get_style_context().add_class('meta')

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.pack_start(loading_lbl, True, True, 0)
    scroll.add(box)
    win.add(scroll)

    # Close on Escape or click
    win.connect('key-press-event', lambda w, e:
                w.destroy() if e.keyval == Gdk.KEY_Escape else None)

    win.show_all()

    def load_full():
        pb = _fetch_pixbuf_full(url, session)
        if pb:
            # Scale to screen if too large
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geo = monitor.get_geometry()
            sw = geo.width - 80
            sh = geo.height - 80
            iw, ih = pb.get_width(), pb.get_height()
            scale = min(sw / iw, sh / ih, 1.0)
            if scale < 1.0:
                pb = pb.scale_simple(int(iw * scale), int(ih * scale),
                                     GdkPixbuf.InterpType.BILINEAR)

            def show(pb=pb):
                img_widget.set_from_pixbuf(pb)
                box.remove(loading_lbl)
                box.pack_start(img_widget, True, True, 0)
                win.resize(pb.get_width() + 20, pb.get_height() + 20)
                box.show_all()
            GLib.idle_add(show)
        else:
            GLib.idle_add(loading_lbl.set_text, "Could not load image.")

    threading.Thread(target=load_full, daemon=True).start()


class PostCard(Gtk.Box):
    def __init__(self, post, session, on_navigate):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.post        = post
        self.session     = session
        self.on_navigate = on_navigate
        # Store the URLs this card links to so we can find it on back-nav
        self._link_urls  = {post.get('link',''), post.get('comments_url','')}

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class('card')
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_hexpand(False)

        # Main horizontal layout: text left, image right
        main_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Left column: meta + title + flair + bar
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left_col.set_hexpand(False)

        # Meta row
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        if post.get('subreddit'):
            sr_btn = Gtk.Button(label=f"r/{post['subreddit']}")
            sr_btn.get_style_context().add_class('subreddit')
            sr_btn.set_relief(Gtk.ReliefStyle.NONE)
            sr_btn.connect('clicked', lambda b, sr=post['subreddit']:
                           on_navigate(f"https://old.reddit.com/r/{sr}/"))
            meta_box.pack_start(sr_btn, False, False, 0)
        if post.get('author'):
            lbl = Gtk.Label(label=f"u/{post['author']}")
            lbl.get_style_context().add_class('meta')
            meta_box.pack_start(lbl, False, False, 0)
        if post.get('age'):
            lbl = Gtk.Label(label=f"• {post['age']}")
            lbl.get_style_context().add_class('meta')
            meta_box.pack_start(lbl, False, False, 0)
        left_col.pack_start(meta_box, False, False, 0)

        # Title
        title_btn = Gtk.Button()
        title_btn.set_relief(Gtk.ReliefStyle.NONE)
        title_lbl = Gtk.Label(label=post['title'])
        title_lbl.get_style_context().add_class('title')
        title_lbl.set_line_wrap(True)
        title_lbl.set_line_wrap_mode(2)
        title_lbl.set_xalign(0)
        title_lbl.set_hexpand(False)
        title_lbl.set_size_request(460, -1)
        title_btn.add(title_lbl)
        title_btn.connect('clicked', lambda b: on_navigate(post['link']))
        left_col.pack_start(title_btn, False, False, 0)

        # Flair
        if post.get('flair'):
            fl = Gtk.Label(label=post['flair'])
            fl.get_style_context().add_class('flair')
            fl.set_xalign(0)
            left_col.pack_start(fl, False, False, 0)

        # Video play button
        if post.get('video_url'):
            play_btn = Gtk.Button(label='▶  Play video')
            play_btn.get_style_context().add_class('play-btn')
            play_btn.set_halign(Gtk.Align.START)
            play_btn.set_margin_top(4)
            play_btn.connect('clicked', lambda b, u=post['video_url']: _open_mpv(u))
            left_col.pack_start(play_btn, False, False, 0)

        # Vote bar
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        score_lbl = Gtk.Label(label=f"▲ {post.get('score','•')}")
        score_lbl.get_style_context().add_class('score')
        bar.pack_start(score_lbl, False, False, 0)

        cmt_btn = Gtk.Button(label=f"💬 {post.get('comments','0')} comments")
        cmt_btn.get_style_context().add_class('comments-lbl')
        cmt_btn.set_relief(Gtk.ReliefStyle.NONE)
        cmt_btn.connect('clicked', lambda b: on_navigate(post['comments_url']))
        bar.pack_start(cmt_btn, False, False, 0)
        left_col.pack_start(bar, False, False, 0)

        main_row.pack_start(left_col, False, False, 0)
        card.pack_start(main_row, False, False, 0)

        # Image: fetch to determine size, then place accordingly
        # Small (<=300px wide) -> right of title row
        # Large (>300px wide)  -> full width below title row
        if post.get('img_url'):
            self.img_widget = Gtk.Image()
            self.img_widget.set_valign(Gtk.Align.START)
            img_eb = Gtk.EventBox()
            img_eb.add(self.img_widget)
            img_eb.set_valign(Gtk.Align.START)
            img_eb.connect('button-press-event',
                           lambda w, e, u=post['img_url']: _show_zoom_window(u, session))
            img_eb.set_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                              Gdk.EventMask.ENTER_NOTIFY_MASK |
                              Gdk.EventMask.LEAVE_NOTIFY_MASK)
            img_eb.connect('enter-notify-event',
                           lambda w, e: w.get_window() and
                           w.get_window().set_cursor(
                               Gdk.Cursor.new_from_name(Gdk.Display.get_default(), 'zoom-in')))
            img_eb.connect('leave-notify-event',
                           lambda w, e: w.get_window() and
                           w.get_window().set_cursor(None))
            self._img_eb   = img_eb
            self._main_row = main_row
            self._card     = card
            threading.Thread(target=self._load_img, daemon=True).start()

        self.set_hexpand(False)
        self.set_size_request(860, -1)
        self.pack_start(card, False, False, 0)

    def _load_img(self):
        pb = _fetch_pixbuf(self.post['img_url'], self.session, max_w=640)
        if not pb:
            return
        w, h = pb.get_width(), pb.get_height()
        if w > 300:
            # Large image — scale to card width and place below title
            max_w = 660
            if w > max_w:
                pb = pb.scale_simple(max_w, int(h * max_w / w),
                                     GdkPixbuf.InterpType.BILINEAR)
            def place_below(pb=pb):
                self._img_eb.set_margin_top(6)
                self._card.pack_start(self._img_eb, False, False, 0)
                self.img_widget.set_from_pixbuf(pb)
                self._card.show_all()
            GLib.idle_add(place_below)
        else:
            # Small thumbnail — keep on the right
            if w > 200:
                pb = pb.scale_simple(200, int(h * 200 / w),
                                     GdkPixbuf.InterpType.BILINEAR)
            def place_right(pb=pb):
                self._img_eb.set_margin_start(4)
                self._main_row.pack_end(self._img_eb, False, False, 0)
                self.img_widget.set_from_pixbuf(pb)
                self._main_row.show_all()
            GLib.idle_add(place_right)


class RedditApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Reddit")
        self.set_default_size(720, 920)
        self.session     = requests.Session()
        self.session.headers.update({'User-Agent': UA})
        self.history     = []
        self.current_url = REDDIT_HOME
        self.loading     = False
        self.cards       = []
        self.next_url    = None
        # Cache: url -> {'posts': [...], 'next_url': ..., 'scroll': float}
        self._page_cache  = {}
        self._last_clicked = None

        self.connect('destroy', Gtk.main_quit)
        self._build_ui()
        self._load(REDDIT_HOME, push=False)

    def _build_ui(self):
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root_box)

        # Sort + subreddit bar
        sub_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sub_bar.get_style_context().add_class('subbar')

        # Back as text label
        back_lbl = Gtk.Label(label='◀')
        back_lbl.get_style_context().add_class('back-label')
        back_eb = Gtk.EventBox()
        back_eb.add(back_lbl)
        back_eb.connect('button-press-event', lambda w, e: self._back())
        sub_bar.pack_start(back_eb, False, False, 0)

        sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sub_bar.pack_start(sep1, False, False, 2)

        sr_lbl = Gtk.Label(label='r/')
        sr_lbl.get_style_context().add_class('sr-label')
        sub_bar.pack_start(sr_lbl, False, False, 0)

        self._all_subs = sorted(set([
            "196","2000snostalgia","2010snostalgia","2danimation","3Dprinting","3danimation","3dmodeling","3ds",
            "3dsmax","4chan","80snostalgia","90snostalgia","AWS","Alps","ArtHistory","AskElectronics",
            "AskPhilosophyFAQ","Buddhism","C25K","CPTSD","CalyxOS","Cinema4D","DINK","DIY",
            "ECE","EE","FPGA","GenderCritical","GooglePixel","IKEA","ITManagers","ImaginaryArchitecture",
            "ImaginaryBattlefields","ImaginaryCharacters","ImaginaryLandscapes","ImaginaryMonsters","ImaginaryTechnology","ImaginaryWastelands","JUSTNOFAMILY","JUSTNOMIL",
            "LaserDisc","LateStageCapitalism","LifeProTips","MGTOW","MachinePorn","Music","Peacock","PhilosophyofMind",
            "Pizza","PowerShell","PrequelMemes","RPG","Rockies","SHTF","SRE","SatisfyingAsF",
            "Screenwriting","SequelMemes","ShouldIbuythisgame","SteamDeck","SubredditSimulator","TVDetails","TheWayWeWere","UnresolvedMysteries",
            "WeAreTheMusicMakers","YouShouldKnow","aaf","abandonedporn","ableton","aboringdystopia","abyssinian","accounting",
            "acecombat","acmilan","acnh","acousticguitar","acoustics","acrylicpainting","actionfilms","actuallesbians",
            "actuallyADHD","actuallyautistic","actuaries","addiction","adhd","adoption","adultswim","adventurerace",
            "advice","aerobics","aeropress","afc","afcon","aff","affiliatemarketing","afl",
            "africa","afrobeats","aftereffects","agency","ageofsigmar","agi","agnosticism","agricole",
            "airbnb","airfryer","airports","aivideo","ak47","albumaday","alcoholism","algebra",
            "algeria","algorithms","algotrading","aliens","all","altcoin","ama","amapiano",
            "amaro","amateur_art","amateurscience","amazonprimevideos","amazonstudios","ambient","amdradeon","amitheasshole",
            "amsterdam","analog","analogsynth","anarchism","anarchocapitalism","andes","android","androiddev",
            "androidgaming","angular","animalcrossing","animalsbeingbros","animalsbeingderps","animalsbeingjerks","animation","anime",
            "anorexiarecovery","ansible","answers","anthropic","anthropology","antinatalism","antiques","antiquing",
            "antiwork","anxiety","apache","aperitivo","apexlegends","appalachian","apple","applemusic",
            "appletv","appliedmathematics","aquarium","aquariums","aquatics","aquavit","ar15","arak",
            "arcade","arcane","archaeology","archery","architecture","architectureporn","archlinux","arduino",
            "argentina","arkhamhorror","armagnac","armoredcore","aromatherapy","arrested_development","arsenal","arsenalfc",
            "art","art_deco","artchallenge","artdeco","artfundamentals","arthouse","artvsartist","asexuality",
            "ashtanga","asia","ask","askacademia","askaustralia","askbaking","askcanada","askcarguys",
            "askculinary","askdocs","askelectronics","askengineers","askeurope","askfeminists","askgames","askhistorians",
            "askindians","asklinguistics","askmen","askmenadvice","askmenover30","asknetsec","askoldpeople","askphilosophy",
            "askprogramming","askpsychology","askreddit","askscience","askstatistics","asktechnology","askteenagers","asktravel",
            "askuk","askwomen","askwomenadvice","askwomenover30","assholedesign","astrology","astronomy","astrophotography",
            "astrophysics","at","atheism","athletics","atlanta","atletico","atpl","attackontitan",
            "auckland","audiodrama","audiophile","augmentedreality","ausfinance","austin","australia","austria",
            "autism","autos","aviation","avid","awardtravel","awk","aws","aww",
            "axolotls","ayax","azure","bacanora","bachata","backcountry","backend","backgammon",
            "backpacking","badeconomics","badhistory","badlegaladvice","badmathematics","badmenosanatomy","badminton","badphilosophy",
            "badpolitics","badscience","badwomenstanatomy","bafta","baijiu","baile","baking","baldursgate3",
            "ballpythons","bambulab","bandcamp","bangladesh","banjo","banking","barcelona","baroque",
            "baseball","bash","basketweaving","bass","bassfishing","bassguitar","batik","battlefield",
            "battlestations","bayernmunich","bbq","bbqporn","beachvolleyball","beadwork","beagles","beardeddragons",
            "bears","beatmaking","beatsaber","bedroom_pop","beer","bees","beetles","behavioraleconomics",
            "behringer","belfast","belgium","benfica","bengalcats","bepop","berlin","betamax",
            "bettafish","betterCallSaul","bettereveryloop","beyondthebump","biathlon","bicycling","bigband","bigcats",
            "bikecommuting","bikram","billiards","biochemistry","biodynamicwine","biohacking","bioinformatics","biology",
            "biologyhumor","biotechnology","bipolarreddit","birding","birmingham","bisexual","bitcoin","bitsy",
            "bitters","bjj","blackandwhite","blackarch","blackholes","blackmetal","blackmirror","blacksmithing",
            "blacktea","bladesmithing","blast","bleach","blended","blender","blockchain","bloodborne",
            "blop","bluegrass","blueorigin","blues","blueteam","bluewater","blursedimages","bmx",
            "boardgames","boas","boating","bobsled","bodybuilding","bodyweightfitness","bogleheads","bohemian",
            "bojackhorseman","bonds","boneappletea","bonehurtingjuice","bonsai","bookbinding","books","bordercollie",
            "borussia","bossa","boston","botany","bouldering","bourbon","bovidae","bowhunting",
            "boxing","brazil","brazilianfood","breadit","breadporn","breakingbad","brisbane","bristol",
            "britishproblems","britishsuccess","broadwaymusicals","brussels","brutalist","bucharest","budapest","budget_meals",
            "bugbounty","bugout","buildapc","bulgaria","bulldogs","bun","bundesliga","burgers",
            "bushcraft","buslife","butterflies","bvb","cabaret","cabinlife","cabinporn","cableporn",
            "cachaca","cajunfood","cakeporn","calculus","calgary","california","calligraphy","callofduty",
            "calvados","cambodia","campers","camping","canada","candles","cannes","canning",
            "canoe","canpolitics","cantopop","capitalism","carboncapture","cardano","cardiff","cardiology",
            "career","careerguidance","caribbeanfood","carporn","cars","cartoonnetwork","cartoons","carving",
            "casio","caskstrength","cassandra","cassettes","castironskillet","casualconversation","catamaran","catan",
            "catpictures","cats","ccie","ccna","ccnp","ccw","cdjs","cdt",
            "celeste","celiac","cellbiology","cellobro","celtic","celticsongs","cemu","centrist",
            "ceramics","cetaceans","cfa","cfb","cggeneral","chainmaille","chakras","chameleons",
            "changemyview","chapotrapalehouse","characterdesign","charcoaldrawing","chatgpt","checkers","cheeseboard","cheesecake",
            "cheesemaking","chefknives","chelseafc","chemex","chemicalreactiongifs","chemistry","chemistryvideos","chess",
            "chicago","chickens","childfree","chile","china","chinesefood","chiptune","chiroptera",
            "chocolatiers","choir","choosingbeggars","christchurch","christianity","churning","cicd","cichlids",
            "cider","cidery","cinema4d","cinematography","circuitpython","citieskylines","cityporn","ck3",
            "classic4chan","classical","classicalguitar","classicalmusic","classicwow","clevercomebacks","climatechange","climatescience",
            "climbing","clinicalpsychology","cloudflare","cluedo","cnc","cockatiels","cocktails","codenames",
            "coffee","coffee_snobs","coffeeroasting","cognac","cogscience","coldbrew","collegebasketball","colombia",
            "colorizedhistory","combinatorics","comedycemetery","comedyfilms","comedyheaven","comedynecromancy","comfyui","comicbooks",
            "comics","commandline","commentary","commodities","communism","community","compsci","comptia",
            "computationalbiology","concacaf","concealedcarry","conceptart","condensedmatter","confession","confidentlyincorrect","conlangs",
            "conmebol","consciousness","conservancy","conservative","conspiracy","conspiracytheories","contemporary","conures",
            "cooking","coolJazz","cooperative","copadelmundo","copenhagen","cordage","corgi","corgis",
            "cork","cornsnakes","cosmology","cosplay","cosplaycostumes","costumes","country","cowboybebop",
            "cows","cozyhome","cozyplaces","cpa","cpl","cpop","cpp","craftbeer",
            "craftsman","creatine","creditcards","creepypasta","cricket","crime","criminalminds","criminalpsychology",
            "criminology","cringe","crispr","criterion","croatia","crochet","crosscountry","crossfit",
            "crpg","crt","cruising","crunchyroll","crusaderkings","cryonics","cryptocurrency","cryptomarkets",
            "crystals","cscareerquestions","csharp","cults","cumbia","cura","curling","cursedcomments",
            "cursedimages","cxbx","cyberpunkgame","cybersecurity","cycling","czechrepublic","dachshunds","daddit",
            "dailyfantasy","dairyfree","dallas","dalle2","damnthatsinteresting","dankmemes","darkmatter","darksouls",
            "darksouls2","darksouls3","dart","darts","datahoarder","dataisbeautiful","datascience","dating",
            "datingadvice","davinciresolve","dc","deadbedrooms","deadspace","deathmetal","debian","decoratedcakes",
            "deepdishpizza","deepfriedmemes","deephouse","deeplearning","deeprockgalactic","deepsea","deerhunting","deezer",
            "defense","defi","degoogle","dehydrating","dehydrator","democrat","demonslayer","demonssouls",
            "denmark","deno","dentistry","denver","depression","dermatology","designdesign","designmyroom",
            "desmume","detroit","developmentalbiology","devops","dfir","dfs","diabeticfood","diablo",
            "diablo2","diablo3","diablo4","diabloIV","dietetics","digestivo","digitalart","digitaldrawing",
            "digitalnomad","dinghy","diorama","dioramas","discus","disney","disneyplus","disneyplusshow",
            "distilling","distrohopping","dividends","diving","divorce","dixit","diyaudio","diyelectronics",
            "djing","dnb","dnd","dndmaps","dobro","docker","docudramas","documentary",
            "dogpictures","dogs","dolphin","dolphins","dominos","dontyouknowwhoiam","doom","dortmund",
            "dota2","dotfiles","doublebass","dragonball","dragonflybsd","dramafilms","draughts","drawing",
            "drawingchallenges","dreamhack","dreampop","dreamworks","dropshipping","drumandbass","drumkits","drumming",
            "drums","ds","dublin","dubstep","duckhunting","ducks","dune","dungeonsanddragons",
            "duolingo","dutchoven","dwarf_fortress","dyeing","dyinglight","dynamodb","earthporn","eartraining",
            "eatcheapandhealthy","eatsandwiches","ebguaranty","ebikes","eclectic","ecology","ecommerce","econmonitor",
            "economics","economy","ecuador","edc","edinburgh","edmonton","edmproduction","edrecovery",
            "egypt","elasticsearch","eldenring","electricalengineering","electricguitar","electricvehicles","electroforming","electromagnetism",
            "electronics","elementaryos","emacs","embeddedlinux","embroidery","emergencymedicine","emmys","emo",
            "emulation","enamel","ender3","energystorage","engineeringmemes","engraving","entitledparents","entitledpeople",
            "entomology","entrepreneur","environmentalscience","epidemiology","epigenetics","epistemology","equestrian","equidae",
            "eredivisie","esa","esl","esp32","esp8266","esphome","esport","esports",
            "espresso","essentialoils","estatesales","estonia","etching","etfs","ethereum","ethics",
            "ethiopia","ethiopianfood","ethnomusicology","etymology","eu4","eupersonalfinance","europe","europeanpolitics",
            "eurorack","euros","evangelion","evertonfc","everydaycarry","evolution","excgarated","exchristian",
            "existentialism","exjw","exmormon","exmuslim","exoplanets","expats","experimental","explainlikeimfive",
            "extremelyinfuriating","eyebleach","f1fantasy","fabrication","faceit","facepalm","factorio","familyguy",
            "fanart","fantasy","fantasyWorldbuilding","fantasybaseball","fantasycricket","fantasyfootball","fantasyhockey","fantasysports",
            "fantasywriters","farmhouse","fatfire","fedora","felidae","femalelivingspace","feminism","fencing",
            "fermentation","ferrets","feyenoord","fibercrafts","fiction","fieldhockey","fifaworldcup","fightporn",
            "filigree","filmanalysis","filmfestivals","filmmaking","filmphotography","filmtheory","finalcut","finalfantasy",
            "financialcareers","financialindependence","financialplanning","findareddit","finland","fire","firearms","firebase",
            "fireemblem","firstimpressions","firsttimebuyer","fish","fishing","fishkeeping","fitness","fixedgear",
            "fl_studio","flightradar","flightsim","flipping","floof","florida","fluidmechanics","flute",
            "flutter","flyfishing","flying","folk","food","foodallergies","foodpics","football",
            "foraging","foreignpolicy","forensics","forex","forhire","formula1","forró","fortifiedwine",
            "fortnite","fosterparents","foundation","foundthemobileuser","foxes","fpga","fpl","france",
            "freebsd","freediving","freejazz","freelance","freeski","freesoftware","frenchbulldogs","frenchcooking",
            "frenchpress","freshwater","fromsoftware","frontend","frugal","fuckcars","fullmetalalchemist","fullstack",
            "funkyhouse","funny","furniture","futurama","futurology","gadgets","gainit","galway",
            "gameboy","gamecollecting","gamecube","gamedesign","gamedev","gameofthrones","games","gaming",
            "gamingsales","gamingsuggestions","garage","garageSales","gardening","gatekeeping","gaybros","gba",
            "gcp","geckos","gelato","gemstones","gemtracks","genetics","genever","genomics",
            "genshin_impact","gentoo","geology","geometry","geopolitics","germanshepherd","germany","getmotivated",
            "ghana","ghibli","ghosts","gifs","gifsthatkeepongiving","gin","git","github",
            "gitlab","glasgow","glassblowing","glitch_in_the_matrix","globaloffensive","globalwarming","gloomhaven","glutenfree",
            "go","godot","golang","gold","goldenretriever","goldenretrievers","goldfish","goldsmithing",
            "golf","googlecloud","gpumarket","grafana","grammar","grapheneos","graphicNovels","graphicdesign",
            "grappa","gravel","gravelbike","greece","greentea","greentext","grep","grief",
            "grilling","grillmasters","grime","growmybusiness","guitarcovers","guitargeek","guitarlessons","guitarpedals",
            "guix","guncontrol","gundeals","guns","gymnastics","hacking","hackthebox","hades",
            "haiku","halflife","halo","hamradio","hamsters","handball","hardbop","hardcore",
            "hardware","hardwarehacking","harpist","haskell","hbo","hbomax","headphones","healing",
            "healthyeating","hearthstone","helpmefind","helsinki","herbalism","herbaltea","heroes","herpetology",
            "highlife","highqualitygifs","highspeedrail","hiking","himalayas","hinduism","hingeapp","hiphopheads",
            "hireawriter","history","historymemes","historyporn","hmmm","hockey","hoi4","holdmybeer",
            "holdup","hollowknight","homeassistant","homeautomation","homebrewing","homeimprovement","homelab","homeopathy",
            "homeserver","homesteading","hometheater","honkaistarrail","horror","horrorfilms","horrorwriting","horses",
            "hotYoga","houdini","hounds","house","houseofthedragon","houseplants","huevember","huggingface",
            "hulu","humansbeingbros","hungary","hunterxhunter","hunting","husky","hydrogen","hygge",
            "hyperpop","iama","iasip","icecream","icefishing","ichthyology","iem","ifyoulikeblank",
            "iguanas","ihadastroke","illustration","imaginarymaps","immigration","immunology","independentfilm","india",
            "indianfood","indiedev","indiefolk","indiegaming","indieheads","indiepop","indonesia","industrial",
            "industrialporn","indycar","infertility","infographics","infrastructureporn","inktober","insects","insomnia",
            "instantkarma","instantpot","intelalchemist","intentionalliving","inter","interestingasfuck","interiordesign","intermittentfasting",
            "internationalrelations","interviews","intuitiveeating","investing","iosgaming","iphone","ipv6","ireland",
            "irishwhiskey","ironman","islam","israel","italianfood","italki","italy","itcareerquestions",
            "itookapicture","itswooooshwith4os","itu","japan","japandi","japanesefood","japanesewhiskey","java",
            "javascript","jazz","jazzfusion","jenever","jenkins","jewelry","jiujitsu","jobs",
            "jpop","jrpg","judaism","judo","jujutsukaisen","jungle","jupiter","justfuckmyshitup",
            "juventus","kafka","kalilinux","kalita","karate","kayaking","kenya","keto",
            "keyforge","kickboxing","kingsnakes","kirby","kitchenknives","knifemaking","knitting","knitwear",
            "knives","kombucha","korea","koreancooking","korg","kotlin","kpop","krnb",
            "kubernetes","kumis","kungfu","labrador","lacrosse","lagom","laliga","landlord",
            "landscaping","languagelearning","lapidary","larp","lasercutting","lastfm","lasvegas","latestagecapitalism",
            "lathe","latin","latinamerica","latvia","lawenforcement","lawschool","leagueoflegends","leanfire",
            "leangains","learnarabic","learnart","learnchinese","learnfrench","learngerman","learngreek","learnguitar",
            "learnhebrew","learnhindi","learnitalian","learnjapanese","learnkorean","learnlatin","learnprogramming","learnrussian",
            "learnspanish","leatherworking","leeds","legaladvice","legends_of_runeterra","lego","legoMOC","legocity",
            "legoideas","legostarwars","legotechnic","lesbians","letsnotmeet","leveldesign","lgbt","liberal",
            "libertarian","lightathleticstrack","lightrail","ligue1","lineageos","linguistics","linocut","linux",
            "linux4noobs","linuxadmin","linuxgaming","linuxhardware","linuxmasterrace","linuxmemes","linuxquestions","linuxupskillchallenge",
            "lisbon","listentothis","literature","lithuania","liveaboard","liverpool","liverpoolfc","lizards",
            "llm","loadingicon","loadscreens","localllama","lofi","logic","logicandlanguage","logicpro",
            "london","longevity","lorcana","losangeles","loseit","lotr","lotrmemes","lotrthetwoTowers",
            "lovebirds","lovegame","lowsodium","lua","lucidmotors","luge","macaws","machinelearning",
            "machining","macos","macrame","macrobiotic","macroeconomics","macrophotography","madden","madeira",
            "mademesmile","madrid","mahjong","mainecoons","majors","makinghiphop","makingof","malaysia",
            "malelifestyle","malelivingspace","maliciouscompliance","malwareanalysis","mame","mammalogy","mancala","manchester",
            "manchesterunited","mancity","mandolin","mandopop","manga","manhua","manhwa","manjaro",
            "manutd","mapmaking","mapporn","marathon","marinebiology","marinelife","marines","mariokart",
            "marriage","mars","marsala","martialarts","marvel","marvelousdesigner","materialscience","math",
            "mathematics","mathmemes","mathriddles","mathrock","maximalism","maya","maybemaybemaybe","mba",
            "me_irl","mead","mealprep","mechanicalkeyboards","medicalschool","medicine","meditation","melbourne",
            "melonds","memes","mensrights","mentalhealth","merengue","mermay","metabolomics","metagenomics",
            "metal","metaldrumming","metalsmithing","metalworking","metaphysics","meteorology","metro","metroid",
            "metroidvania","mexicanfood","mexico","mezcal","mha","miami","microbialecology","microbiology",
            "microcontrollers","micropython","midcentury","middleeast","middleeasternfood","midjourney","mildlyaesthetic","mildlyinfuriating",
            "mildlyinteresting","mildlyvandalised","miles","military","milling","mindfulness","minecraft","miniatures",
            "minimalism","minimalistroom","minipainting","minneapolis","mint","mixcloud","mixedreality","mlb",
            "mls","mma","mobileGaming","mobilePhotography","mobiledev","modelling","modeltrains","moderatepolitics",
            "moderndesign","modular","moguls","moka","mommit","mongodb","monitors","monohull",
            "monsterhunter","monsterhunterworld","montreal","moog","moon","moralphilosophy","morocco","morphs",
            "mortgages","mosaic","moths","motiondesign","motocross","motorcycles","mountainbiking","mountaineering",
            "mountains","movies","mpb","mqtt","msfs","msp","mtg","muaythai",
            "murderMystery","murderedbywords","musical","musicals","musicproduction","musicsuggestions","musictheory","mustelids",
            "mutualfunds","muzzleloaders","myanmar","mycology","mysql","mystery","n64","naf",
            "namethatsong","nanotechnology","napoli","narcissisticabuse","naruto","nasa","nascar","nashville",
            "natalism","nationalparks","nato","nattyorjuice","naturalDye","naturalbodybuilding","naturalhealth","naturalwine",
            "nature","nba","nbaallstar","nbadraft","nbatrade","ncaabasketball","ncaafootball","neapolitanpizza",
            "needadvice","negotiation","neoliberal","neovim","nepal","nes","netbsd","netflix",
            "netflixseries","netherlands","netsec","netsecstudents","networking","neurology","neuropsychology","neuroscience",
            "neutralnews","news","newwackytemplate","newyork","newzealand","nextfuckinglevel","nextjs","nfl",
            "nfl2k","nflmocks","nfloffseason","nfts","nginx","nhl","nickelodeon","nier",
            "nigeria","nightphotography","nintendo","nioh","nitro","nixos","nll","nodejs",
            "noisygifs","nonbinary","nonononoyes","nootropics","northernireland","norway","noscraps","nosleep",
            "nostalgicgaming","nostupidquestions","notinteresting","notjustbikes","nottheonion","nrl","nuclear","nuke",
            "numbertheory","numerology","nursing","nutrition","nuxtjs","nvidiageforce","nwordcountbot","nwsl",
            "nyc","observability","ocaml","ocd","oceaniafootball","oceanography","octopi","octoprint",
            "oddlysatisfying","offgrid","offgridliving","oilpainting","okcupid","oldschoolcool","ollama","olympics",
            "olympicweightlifting","oncology","one","onebag","onepiece","oneplus","oolongtea","openai",
            "openbsd","opengl","opensource","openvpn","openwater","opera","opnsense","optics",
            "options","orienteering","origami","ornithology","oscars","oscp","oslo","othello",
            "ottawa","outoftheloop","outside","ouzo","overemployed","overwatch","overwatch2","padel",
            "painting","pakistan","paleo","paleontology","pandas","papercrafting","para","parakeets",
            "paralympics","paramount","paranormal","parenting","paris","parks","parrotos","parrots",
            "particlephysics","partyparrot","passive_income","pasta","pastry","pathfinder","pathofexile","patientgamers",
            "patternmaking","pcgaming","pcmasterrace","pcsx2","pct","pcvr","pedals","penguins",
            "pennystocks","penology","pensions","pentathlon","pentesting","perfectloops","perfecttiming","performancenutrition",
            "perl","permaculture","persiancat","persona5","personalfinance","perth","peru","petsofreddit",
            "pettyrevenge","pff","pfl","pfsense","pharmacy","phenomenology","philippines","philosophy",
            "philosophyofscience","pho","phoenix","phonk","photocritique","photography","photojournalism","photoshopbattles",
            "physics","physicsgifs","physicsmemes","piano","pickleball","pickling","pico8","pics",
            "pikmin","pilottraining","pinball","piracy","pisco","pistols","pitbulls","pittsburgh",
            "pixar","pixelart","pizza","place","plan9","plantbased","plantbiology","plantedtank",
            "platformio","plating","playstation","pll","pocketknives","podcasts","poe2","poetry",
            "pokemon","pokemontcg","poker","poland","politicalphilosophy","politics","polkadot","polyamory",
            "polyglot","polymers","pomeranians","popheads","popos","popular","portWine","portablegaming",
            "portal","portland","porto","portraitphotography","portugal","postgres","postrock","pottery",
            "pourover","povertyfinance","powder","powerlifting","ppl","ppsspp","prague","pregnant",
            "premiere","premierleague","preppers","prepping","pressurecooking","preworkout","primatology","primevideos",
            "printmaking","privacy","privacytoolsIO","productivity","programmerhumor","programming","progressive","progrock",
            "prometheus","promptengineering","proofs","propmaking","prorevenge","proteinshakes","proteomics","protondb",
            "protools","proxmox","prusa","ps2","ps3","ps4","ps5","psg",
            "psp","psvr","psych","psychiatry","psychology","ptsd","pu_erh","pubg",
            "publicfreakout","publichealth","publictransit","pugs","pulque","punk","puremathematics","purplepilldebate",
            "pygame","pyrography","python","pytorch","qigong","qobuz","quake","quantumcomputing",
            "quantumphysics","qubes","questpro","quilting","quityourbullshit","rabbitmq","rabbits","racquetball",
            "radiology","radioplay","ragdoll","raicilla","railfans","railroading","raisedbynarcissists","rallying",
            "ramen","randnsfw","random","rangers","rareinsults","rarepuppers","raspberry_pi","raspberrypi",
            "rateyourmusic","rawfood","reactjs","realestate","realestateinvesting","realmadrid","recession","recipes",
            "recoverywarriors","redis","redteam","reef","reggae","reiki","rekordbox","relationship_advice",
            "relationships","religion","reloading","remix","remotework","renewableenergy","reptiles","republican",
            "residentevil","resin","resinJewelry","resincraft","restorative","resumes","retirement","retroarch",
            "retrogaming","reverseengineering","reversi","revolvers","rfid","rhum","rickandmorty","rigging",
            "rimworld","rivian","rnb","roadcycling","roadtrip","roblox","robotics","rockclimbing",
            "rocketlab","rodents","rogaining","roguelikes","roguelite","roland","roma","romancefilms",
            "romania","romantic","rome","roommakeovers","ropework","rowing","rpcs3","rpg",
            "rpgmaker","rtos","ruby","rugby","rugbyleague","rugbyunion","rum","running",
            "ruralporn","rushwork","russia","rust","rvliving","rye","ryujinx","s1mple",
            "sadcore","sailing","sake","salsa","saltwaterfishing","samba","sampling","samsung",
            "sandman","sanfrancisco","sankey","sarms","satisfactory","saturn","saxophone","scandinaviandesign",
            "schalke","science","sciencememes","scifi","scififilms","scifiwriting","scotch","scotland",
            "scottishfold","scratching","screenprinting","screenshotsaturday","screenwriting","screenwritingadvice","scripting","scuba",
            "sculpting","sculpture","sdl","sdr","seattle","secondamendment","securityCTF","sed",
            "seinfeld","seismology","sekiro","selfdiagnosis","selfhosted","serato","serbia","serialkillers",
            "seriea","severance","sevilla","sewing","sewingpatterns","sfml","shareyourmusic","sharks",
            "sheffield","sherry","shibainu","shitpostcrusade","shitposting","shochu","shoegaze","shoegazing",
            "shogi","shortfilms","shortscarystories","shotguns","showerthoughts","shrimptank","siamese","sideproject",
            "silenthill","silk","silver","silversmithing","simpleliving","simpsons","sims","singapore",
            "singleMalt","singledads","singlemoms","singlespeed","singularity","siphon","sixnations","skateboarding",
            "skeleton","skeptic","sketching","skiing","skoolieconversion","skyporn","slackware","slasher",
            "slavelabour","sleep","slowcooking","slowcore","smallbusiness","smallpetbirds","smallstreetbets","smarthome",
            "smashbros","smoker","smoking","snakes","snes","snooker","snorkeling","snowboarding",
            "soap","soccer","socialism","socialpsychology","socialsecurity","softwaregore","soju","solana",
            "solar","solidity","solotravel","songwriting","sotol","soul","soulslike","soundcloud",
            "soups","sourdough","sousvide","southafrica","southernfood","southpark","space","spaceporn",
            "spacex","spain","spaniels","spartan","speakerbuilding","spearfishing","speedpaint","speedrun",
            "sphynx","spiders","spinning","spirituality","splatoon","sportfishing","sporting","sports",
            "sportsbetting","sportsdietetics","spotify","spurs","squash","srilanka","stablediffusion","stainedglass",
            "starcraft","starcraft2","stardewvalley","starfinder","starterpacks","startrek","startups","starwars",
            "stateParks","statistics","steak","steam","steamdeck","steamvr","stellaris","stepparents",
            "steroids","stockholm","stocks","stoicism","stonecarving","stopdrinking","stopmotion","strangerthings",
            "streetphotography","strongman","strongtowns","structuralbiology","subnautica","subredditdrama","substancepainter","subways",
            "succession","succulents","sugarart","sundance","supabase","superbikes","supermariomaker","supplements",
            "suppressor","surfing","surgery","surreal","survival","survivinginfidelity","survivorsofabuse","sushi",
            "sustainability","svelte","swap","sweatystartup","sweden","swift","swimming","swimmingathletics",
            "swingmusic","switch","switzerland","sydney","synchronised","synthesizers","syntheticbiology","sysadmin",
            "systemsbiology","tabletennis","tabletop","tacos","taekwondo","taichi","tailoring","tails",
            "tailscale","taiwan","talesfromretail","talesfromtechsupport","talesfromthefrontdesk","talesfromyourserver","tanzania","taoism",
            "tapas","tarotcards","tasmota","tax","tea","teamfighttactics","technicallythetruth","techno",
            "technology","techsupport","techsupportgore","television","tenant","tennessee","tennis","tensorflow",
            "tequila","terraform","terraria","terriblefacebookmemes","terriers","teslamotors","texas","tf2",
            "thailand","thairecipes","thanosdidnothingwrong","theRedPill","theater","theboys","theexpanse","thelongdark",
            "theoffice","therestofthefuckingowl","therewasanattempt","thermodynamics","thetagang","thetruthishere","thewheeloftimeTV","theydidthemath",
            "thrash","threejs","thrifting","thriftstorehauls","thriller","tidal","tie_dye","tiff",
            "tifu","timelapse","tinder","tinyhomes","tinyhouses","tipofmytongue","tm","tmux",
            "todayilearned","toolrestoration","toomeirlevel100","topology","toronto","totalwar","tottenham","tough_mudder",
            "trad","traditionalmedia","traditionalmusic","trailrunning","trails","trains","trainspotting","trampolining",
            "trams","trance","transcendental","transgender","transhumanism","transportfever","trapproduction","travel",
            "triathletes","triathlon","trucks","truecrime","truecrimeDiscussion","truenas","trumpet","tryhackme",
            "tryingforababy","turning","turtles","twoplayer","twoxchromosomes","typescript","typography","ubuntu",
            "ufc","ufo","uganda","uidesign","uk_house","uknews","ukpersonalfinance","ukpolitics",
            "ukraine","ukulele","ultralight","ultramarathon","ultrarunning","ultrawidemasterrace","underground","underwater",
            "unethicalLifeProTips","unexpected","unitedkingdom","unity3d","unix","unixporn","unpopularopinion","unrealengine",
            "unresolvedmysteries","unsolvedmysteries","unstirredpaint","upliftingnews","upright","urbanism","urbanplanning","uspolitics",
            "ussoccer","uxdesign","v60","valorant","valueinvesting","vancouver","vanlife","vans",
            "vaporwave","vegan","veganfoodporn","veganrecipes","vegetarian","velominati","venezuela","vermouth",
            "veterinary","vfx","vhscollectors","victoria3","victorian","videoediting","videography","vienna",
            "vietnam","vim","vintageDecor","vintageads","vintagegaming","vinyasa","vinyl","vinylcollectors",
            "violalegend","violin","violinist","vipassana","virgingalactic","virology","virtualreality","visualeffects",
            "visualnovels","vita","vmware","void","volcanology","volleyball","voron","vrgaming",
            "vscode","vuejs","vulkan","wales","wallstreetbets","war","wargames","warhammer",
            "warhammer40k","warsaw","warzone","water_polo","watercolor","waterfowl","waterporn","weaving",
            "web3","webcomic","webcomics","webdesign","webdev","webgl","webtoon","wedding",
            "weddingplanning","weightlifting","weightroom","weirddalle","welding","wellington","wellworn","westafricanfood",
            "westend","whales","whatcouldgowrong","whatisthisthing","whatisthisworth","whatsthisbird","whatsthisbug","whatsthisplant",
            "whatsthisrock","whisky","whitetea","whitewater","whole30","wholesomememes","whonix","widowers",
            "wii","wiiu","wildcrafting","wildflowers","wildlife","wildlifephotography","willow","wind",
            "windows","windows10","windows11","wine","winemaking","wingspan","winnipeg","wireguard",
            "wirewrapping","witcher","wnba","wok","wolves","woodburning","woodcarving","woodfired",
            "woodworking","woooosh","workfromhome","workreform","worldbuilding","worldmusic","worldnews","worldofwarcraft",
            "worldpolitics","worlds","wow","wowclassic","wrestling","writing","xbox","xboxgamepass",
            "xboxone","xboxseriesx","xcom","xemu","xenia","xenoblade","xfl","xiangqi",
            "xplane","yamaha","yesyesyesno","yin","ynab","yoga","youtube","youtubers",
            "yugioh","yuzu","zambia","zbrush","zelda","zenBuddhism","zerotier","zerowaste",
            "zerowastelife","zig","zigbee","zimbabwe","zoology","zoomies","zoos","zorin",
            "zsh","zurich","zwave",
        ]))

        self.sr_entry = Gtk.Entry()
        self.sr_entry.get_style_context().add_class('sr-entry')
        self.sr_entry.set_width_chars(16)
        self.sr_entry.connect('activate', lambda e: self._go_sr_and_hide())
        self.sr_entry.connect('key-press-event', self._on_sr_key)
        self.sr_entry.connect('changed', lambda e: GLib.idle_add(self._style_ac_popup))
        completion = Gtk.EntryCompletion()
        ac_model = Gtk.ListStore(str)
        for s in self._all_subs:
            ac_model.append([s])
        completion.set_model(ac_model)
        completion.set_text_column(0)
        completion.set_minimum_key_length(1)
        completion.set_inline_completion(False)
        completion.set_popup_completion(True)
        completion.set_popup_set_width(False)
        completion.connect('match-selected', self._on_ac_match)
        completion.connect('notify::popup-set-width', self._style_ac_popup)
        self.sr_entry.set_completion(completion)
        # Style popup once it's realized
        GLib.idle_add(self._style_ac_popup)
        sub_bar.pack_start(self.sr_entry, False, False, 0)

        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sub_bar.pack_start(sep2, False, False, 4)

        # Sort labels
        self.sort_labels = {}
        self.active_sort = 'hot'
        for sort in SORTS:
            lbl = Gtk.Label(label=sort.capitalize())
            lbl.get_style_context().add_class(
                'sort-label-active' if sort == self.active_sort else 'sort-label')
            eb = Gtk.EventBox()
            eb.add(lbl)
            eb.connect('button-press-event', self._on_sort_click, sort, lbl)
            sub_bar.pack_start(eb, False, False, 0)
            self.sort_labels[sort] = lbl

        root_box.pack_start(sub_bar, False, False, 0)

        # Scrolled feed — feed_box is the sole child so scroll works normally
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_kinetic_scrolling(True)
        scroll.set_propagate_natural_width(True)
        scroll.connect('edge-reached', self._on_edge_reached)
        scroll.get_vadjustment().connect('value-changed', self._on_scroll_changed)

        # feed_box sits inside a centred wrapper so margins are stable
        # and never affected by the size-request clamp.
        self.feed_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.feed_wrapper.set_hexpand(False)
        self.feed_wrapper.set_halign(Gtk.Align.CENTER)
        self.feed_wrapper.set_margin_start(16)
        self.feed_wrapper.set_margin_end(16)

        self.feed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.feed_box.set_hexpand(False)
        self.feed_box.set_halign(Gtk.Align.CENTER)
        self.feed_box.set_size_request(860, -1)


        self.feed_wrapper.pack_start(self.feed_box, True, True, 0)
        scroll.add(self.feed_wrapper)
        root_box.pack_start(scroll, True, True, 0)
        self.scroll = scroll

        # Status bar
        self.status_lbl = Gtk.Label(label='Loading…')
        self.status_lbl.get_style_context().add_class('statusbar')
        self.status_lbl.set_xalign(0)
        root_box.pack_start(self.status_lbl, False, False, 0)

        self.show_all()

    def _on_feed_allocate(self, widget, alloc):
        pass  # unused

    def _style_ac_popup(self, *args):
        """Style EntryCompletion popup by finding it among toplevel windows."""
        provider = Gtk.CssProvider()
        provider.load_from_data(b"* { background-color: #24273a; color: #cdd6f4; }")
        def apply_to(w):
            w.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            if hasattr(w, "foreach"):
                w.foreach(apply_to)
        for w in Gtk.Window.list_toplevels():
            if w is not self and w.get_visible():
                apply_to(w)

    def _on_sr_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.sr_entry.set_text('')

    def _on_ac_match(self, completion, model, iter_):
        sr = model[iter_][0]
        self.sr_entry.set_text(sr)
        self._load(f"https://old.reddit.com/r/{sr}/")
        return True

    def _clear_feed(self):
        for c in self.cards: c.destroy()
        self.cards.clear()
        for child in self.feed_box.get_children():
            child.destroy()

    def _navigate(self, url):
        if not url: return
        if not url.startswith('http'): url = 'https://' + url
        self._last_clicked = url
        self._load(url)

    def _go_sr(self):
        sr = self.sr_entry.get_text().strip().lstrip('r/').lstrip('/')
        if sr: self._load(f"https://old.reddit.com/r/{sr}/")

    def _go_sr_and_hide(self):
        self._go_sr()


    def _on_sort_click(self, widget, event, sort, lbl):
        for s, l in self.sort_labels.items():
            l.get_style_context().remove_class('sort-label-active')
            l.get_style_context().add_class('sort-label')
        lbl.get_style_context().remove_class('sort-label')
        lbl.get_style_context().add_class('sort-label-active')
        self.active_sort = sort
        base = re.sub(r'/(best|hot|new|top|rising)/?$', '/', self.current_url)
        if 'reddit.com' in base:
            self._load(base.rstrip('/') + f'/{sort}/')

    def _back(self):
        if self.history:
            self._load(self.history.pop(), push=False)

    def _set_status(self, msg):
        GLib.idle_add(self.status_lbl.set_text, msg)

    def _load(self, url, push=True):
        if self.loading: return
        self.loading = True
        url = re.sub(r'https?://(www\.|new\.)?reddit\.com',
                     'https://old.reddit.com', url)
        # Save scroll position of page we are leaving
        if self.current_url:
            adj = self.scroll.get_vadjustment()
            if self.current_url in self._page_cache:
                self._page_cache[self.current_url]['scroll'] = adj.get_value()
        if push and self.current_url:
            self.history.append(self.current_url)
        self.current_url = url
        # Serve from cache if available (instant back navigation)
        if url in self._page_cache:
            cached = self._page_cache[url]
            GLib.idle_add(self._render, cached['posts'], url,
                          cached['next_url'], cached['scroll'])
            return
        self._set_status('Loading…')
        threading.Thread(target=self._fetch, args=(url,), daemon=True).start()

    def _fetch(self, url, append=False):
        try:
            r = self.session.get(url, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            if soup.select('.thing.link') and not soup.select_one('.commentarea'):
                posts, next_url = self._parse_listing(soup, url)
                if append:
                    GLib.idle_add(self._append_posts, posts, next_url)
                else:
                    GLib.idle_add(self._render, posts, url, next_url)
            elif soup.select_one('.commentarea') or soup.select_one('.thing.link'):
                post, comments = self._parse_comments(soup, url)
                GLib.idle_add(self._render_comments, post, comments, url)
            else:
                webbrowser.open(url)
                self._set_status(f'Opened in browser: {url}')
                self.loading = False
        except Exception as e:
            self._set_status(f'Error: {e}')
            self.loading = False

    def _parse_listing(self, soup, url):
        posts = []
        for thing in soup.select('.thing.link'):
            title_tag = thing.select_one('p.title a.title')
            if not title_tag: continue
            href = title_tag.get('href', '')
            if href.startswith('/'): href = urljoin(url, href)
            lbl = _clean(title_tag.get_text())
            if not lbl: continue

            sr      = thing.get('data-subreddit', '')
            auth_el = thing.select_one('.tagline a.author')
            auth    = _clean(auth_el.get_text()) if auth_el else ''
            time_el = thing.select_one('.tagline time')
            age     = _reltime(time_el.get('title', '')) if time_el else ''
            fl_el   = thing.select_one('.flair')
            flair   = _clean(fl_el.get_text()) if fl_el else ''

            sc_el = thing.select_one('.score.unvoted,.score.likes,.score.dislikes,.score')
            score = _fmt_score(sc_el.get('title','') or _clean(sc_el.get_text())) if sc_el else '•'

            n_cmt  = _comment_count(thing)
            cmt_a  = thing.select_one('a.comments')
            c_href = urljoin(url, cmt_a.get('href','')) if cmt_a else href

            is_img   = bool(IMAGE_EXT.search(href)) or \
                       bool(re.search(r'(i\.redd\.it|i\.imgur\.com)', href, re.I))
            is_video = bool(VIDEO_URL.search(href)) or \
                       thing.get('data-domain','') == 'v.redd.it'
            link     = c_href if is_img else href

            posts.append({
                'title': lbl, 'link': link,
                'subreddit': sr, 'author': auth, 'age': age, 'flair': flair,
                'score': score, 'comments': n_cmt, 'comments_url': c_href,
                'img_url': _best_preview(thing, url),
                'video_url': href if is_video else None,
            })

        next_btn = soup.select_one('span.next-button a, .next-button a')
        next_url = None
        if next_btn:
            next_url = next_btn.get('href', '')
            if next_url and not next_url.startswith('http'):
                next_url = urljoin(url, next_url)

        return posts, next_url

    def _parse_comments(self, soup, url):
        post = {}
        thing = soup.select_one('.thing.link')
        if thing:
            title_a = soup.select_one('.thing.link p.title a.title')
            if title_a:
                post['title'] = _clean(title_a.get_text())
                post['link']  = urljoin(url, title_a.get('href',''))
            sr      = thing.get('data-subreddit','')
            auth_el = thing.select_one('.tagline a.author')
            time_el = thing.select_one('.tagline time')
            sc_el   = thing.select_one('.score.unvoted,.score.likes,.score.dislikes,.score')
            post['subreddit'] = sr
            post['author']    = _clean(auth_el.get_text()) if auth_el else ''
            post['age']       = _reltime(time_el.get('title','')) if time_el else ''
            post['score']     = _fmt_score(sc_el.get('title','') or _clean(sc_el.get_text())) if sc_el else '•'
            post['img_url']   = _best_preview(thing, url)
            body = thing.select_one('.usertext-body')
            post['selftext']  = _clean(body.get_text()) if body else ''

        comments = []
        for c in soup.select('.commentarea .thing.comment, .commentarea div.comment'):
            body = c.find(class_='usertext-body')
            if not body: continue
            try:    depth = int(c.get('data-depth', 0))
            except: depth = 0
            auth_el = c.find(class_='author')
            sc_el   = c.select_one('.score.unvoted,.score')
            score   = _fmt_score(_clean(sc_el.get('title','') or sc_el.get_text())) if sc_el else ''
            author  = _clean(auth_el.get_text()) if auth_el else '[deleted]'
            text    = _clean(body.get_text())
            if text:
                comments.append({'author': author, 'score': score,
                                 'depth': depth, 'text': text})
        return post, comments

    def _render_comments(self, post, comments, url):
        self._clear_feed()
        adj = self.scroll.get_vadjustment()
        adj.set_value(0)

        if post.get('title'):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.get_style_context().add_class('card')
            card.set_margin_top(4)
            card.set_margin_bottom(4)
            card.set_hexpand(False)

            meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            if post.get('subreddit'):
                lbl = Gtk.Label(label=f"r/{post['subreddit']}")
                lbl.get_style_context().add_class('subreddit')
                meta_box.pack_start(lbl, False, False, 0)
            if post.get('author'):
                lbl = Gtk.Label(label=f"u/{post['author']}")
                lbl.get_style_context().add_class('meta')
                meta_box.pack_start(lbl, False, False, 0)
            if post.get('age'):
                lbl = Gtk.Label(label=f"• {post['age']}")
                lbl.get_style_context().add_class('meta')
                meta_box.pack_start(lbl, False, False, 0)
            card.pack_start(meta_box, False, False, 0)

            title_lbl = Gtk.Label(label=post['title'])
            title_lbl.get_style_context().add_class('title')
            title_lbl.set_line_wrap(True)
            title_lbl.set_line_wrap_mode(2)
            title_lbl.set_xalign(0)
            title_lbl.set_size_request(800, -1)
            card.pack_start(title_lbl, False, False, 0)

            if post.get('img_url'):
                img_widget = Gtk.Image()
                img_widget.set_margin_top(4)
                img_eb = Gtk.EventBox()
                img_eb.add(img_widget)
                img_eb.connect('button-press-event',
                               lambda w, e, u=post['img_url']: _show_zoom_window(u, self.session))
                card.pack_start(img_eb, False, False, 0)
                def _load(w=img_widget, u=post['img_url']):
                    pb = _fetch_pixbuf(u, self.session, max_w=500)
                    if pb: GLib.idle_add(w.set_from_pixbuf, pb)
                threading.Thread(target=_load, daemon=True).start()

            if post.get('selftext'):
                txt = Gtk.Label(label=post['selftext'])
                txt.get_style_context().add_class('meta')
                txt.set_line_wrap(True)
                txt.set_line_wrap_mode(2)
                txt.set_xalign(0)
                txt.set_hexpand(False)
                txt.set_size_request(660, -1)
                card.pack_start(txt, False, False, 0)

            score_lbl = Gtk.Label(label=f"▲ {post.get('score','•')}")
            score_lbl.get_style_context().add_class('score')
            score_lbl.set_xalign(0)
            card.pack_start(score_lbl, False, False, 0)

            self.feed_box.pack_start(card, False, False, 0)
            self.cards.append(card)

        hdr = Gtk.Label(label=f"  💬 {len(comments)} comments")
        hdr.get_style_context().add_class('subreddit')
        hdr.set_xalign(0)
        hdr.set_margin_top(8)
        hdr.set_margin_bottom(4)
        hdr.set_margin_start(16)
        self.feed_box.pack_start(hdr, False, False, 0)

        for c in comments:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row.get_style_context().add_class('card')
            row.set_margin_start(min(c['depth'] * 16, 120))
            row.set_margin_top(2)
            row.set_margin_bottom(2)

            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            author_lbl = Gtk.Label(label=c['author'])
            author_lbl.get_style_context().add_class('comment-author')
            top.pack_start(author_lbl, False, False, 0)
            if c.get('score'):
                sc_lbl = Gtk.Label(label=f"▲ {c['score']}")
                sc_lbl.get_style_context().add_class('comment-score')
                top.pack_start(sc_lbl, False, False, 0)
            row.pack_start(top, False, False, 0)

            txt = Gtk.Label(label=c['text'])
            txt.get_style_context().add_class('comment-text')
            txt.set_line_wrap(True)
            txt.set_line_wrap_mode(2)
            txt.set_xalign(0)
            txt.set_hexpand(False)
            txt.set_size_request(760, -1)
            row.pack_start(txt, False, False, 0)

            row.set_hexpand(False)
            self.feed_box.pack_start(row, False, False, 0)
            self.cards.append(row)

        self.feed_box.show_all()
        self._set_status(f'{len(comments)} comments  —  {url}')
        self.loading = False
        return False

    def _on_edge_reached(self, scroll, pos):
        if pos == Gtk.PositionType.BOTTOM:
            self._load_more()

    def _on_scroll_changed(self, adj):
        """Fallback trigger: fire load-more when within 400px of the bottom."""
        bottom = adj.get_upper() - adj.get_page_size()
        if bottom > 0 and (bottom - adj.get_value()) < 400:
            self._load_more()

    def _load_more(self):
        if self.loading or not self.next_url: return
        self.loading = True
        self._set_status('Loading more…')
        threading.Thread(target=self._fetch, args=(self.next_url, True), daemon=True).start()

    def _append_posts(self, posts, next_url=None):
        self.next_url = next_url
        for post in posts:
            card = PostCard(post, self.session, self._navigate)
            self.feed_box.pack_start(card, False, False, 0)
            self.cards.append(card)
        self.feed_box.show_all()
        total = len(self.cards)
        self._set_status(f'{total} posts loaded  —  {self.current_url}')
        self.loading = False
        # Update cache with all accumulated posts + latest next_url so
        # back navigation restores the full loaded list, not just page 1
        if self.current_url in self._page_cache:
            cached_posts = self._page_cache[self.current_url]['posts']
            scroll_val   = self._page_cache[self.current_url]['scroll']
        else:
            cached_posts = []
            scroll_val   = 0
        self._page_cache[self.current_url] = {
            'posts':    cached_posts + posts,
            'next_url': next_url,
            'scroll':   scroll_val,
        }
        return False

    def _render(self, posts, url, next_url=None, restore_scroll=0):
        self._clear_feed()
        self.next_url = next_url
        # Cache this listing so back navigation is instant
        self._page_cache[url] = {
            'posts': posts, 'next_url': next_url, 'scroll': restore_scroll}
        if not posts:
            lbl = Gtk.Label(label='No posts found.')
            lbl.get_style_context().add_class('meta')
            self.feed_box.pack_start(lbl, False, False, 20)
        else:
            for post in posts:
                card = PostCard(post, self.session, self._navigate)
                self.feed_box.pack_start(card, False, False, 0)
                self.cards.append(card)
        self.feed_box.show_all()
        self._set_status(f'{len(posts)} posts  —  {url}')
        self.loading = False
        if self._last_clicked:
            # Scroll to the card that was clicked, not raw pixels
            GLib.idle_add(self._scroll_to_clicked)
        elif restore_scroll > 0:
            GLib.idle_add(self._restore_scroll, restore_scroll)
        else:
            self.scroll.get_vadjustment().set_value(0)
        return False

    def _restore_scroll(self, value, attempts=0):
        adj = self.scroll.get_vadjustment()
        upper = adj.get_upper() - adj.get_page_size()
        if upper < value and attempts < 20:
            GLib.timeout_add(50, self._restore_scroll, value, attempts + 1)
        else:
            adj.set_value(value)
        return False

    def _scroll_to_clicked(self, attempts=0):
        """Find the card whose link matches _last_clicked and scroll to it."""
        target = self._last_clicked
        if not target:
            return False
        for card in self.cards:
            urls = getattr(card, '_link_urls', set())
            if target in urls:
                # Get card position relative to feed_box
                alloc = card.get_allocation()
                if alloc.y <= 0 and attempts < 20:
                    # Not laid out yet — retry
                    GLib.timeout_add(50, self._scroll_to_clicked, attempts + 1)
                    return False
                # Offset by feed_box margin
                margin = self.feed_box.get_margin_start()
                adj = self.scroll.get_vadjustment()
                # Subtract a bit so the post isn't right at the very top
                target_y = max(0, alloc.y - 60)
                adj.set_value(target_y)
                self._last_clicked = None
                return False
        # Card not found (e.g. comment page clicked) — just keep position
        self._last_clicked = None
        return False

if __name__ == '__main__':
    # Ensure cursor blinks in text entries
    settings = Gtk.Settings.get_default()
    settings.set_property('gtk-cursor-blink', True)
    settings.set_property('gtk-cursor-blink-time', 1000)
    _apply_css()
    app = RedditApp()
    Gtk.main()
