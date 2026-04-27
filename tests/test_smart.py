"""Smart layer unit tests — no device required."""
from strands_adb import smart


def test_parse_notifications():
    raw = """
NotificationRecord(0x08fa4e94: pkg=com.instagram.android user=UserHandle{0} id=64278 tag=direct
      icon=Icon(typ=RESOURCE pkg=com.instagram.android id=0x7f082a2a)
                android.title=String (Nimet | Life in Berlin)
                android.text=String (https://example.com/reel)
                category=msg
NotificationRecord(0x0bf66bde: pkg=com.google.android.gm user=UserHandle{0} id=99
                android.title=String (TAAFT)
                android.text=SpannableString (Weekly AI recap)
                category=email
"""
    parsed = smart.parse_notifications(raw)
    assert len(parsed) >= 2
    titles = [p["title"] for p in parsed]
    assert "Nimet | Life in Berlin" in titles
    assert "TAAFT" in titles
    assert any(p["pkg"] == "com.instagram.android" for p in parsed)


def test_parse_ui_dump():
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <node class="android.widget.TextView" text="Chrome" content-desc=""
        resource-id="com.android.launcher3:id/app_icon" clickable="true"
        focusable="true" enabled="true" bounds="[100,200][300,400]"/>
  <node class="android.view.View" text="" content-desc="Gmail app icon"
        resource-id="" clickable="true" focusable="true" enabled="true"
        bounds="[500,600][700,800]"/>
</hierarchy>"""
    elements = smart.parse_ui_dump(xml)
    assert len(elements) == 2
    chrome = smart.find_element(elements, text="Chrome")
    assert chrome is not None
    assert chrome["bounds"]["cx"] == 200
    assert chrome["bounds"]["cy"] == 300

    gmail = smart.find_element(elements, desc="Gmail")
    assert gmail is not None
    assert gmail["bounds"]["cx"] == 600


def test_parse_thermals():
    raw = """
	Temperature{mValue=28.2, mType=2, mName=battery, mStatus=0}
	Temperature{mValue=83.0, mType=0, mName=BIG, mStatus=0}
	Temperature{mValue=43.5, mType=-1, mName=soc_therm, mStatus=1}
"""
    temps = smart.parse_thermals(raw)
    assert len(temps) == 3
    by_name = {t["name"]: t for t in temps}
    assert by_name["battery"]["value"] == 28.2
    assert by_name["BIG"]["value"] == 83.0
    assert by_name["soc_therm"]["status"] == 1


def test_parse_wifi():
    raw = '''
Wifi is enabled
Wifi is connected to "Verizon_SG4VBJ"
WifiInfo: SSID: "Verizon_SG4VBJ", BSSID: dc:4b:a1:de:e9:4c, IP: /192.168.1.6, RSSI: -44, Link speed: 680Mbps, Frequency: 5785MHz
'''
    info = smart.parse_wifi(raw)
    assert info["connected"] is True
    assert info["ssid"] == "Verizon_SG4VBJ"
    assert info["ip"] == "192.168.1.6"
    assert info["rssi"] == -44
    assert info["link_speed_mbps"] == 680
    assert info["frequency_mhz"] == 5785


def test_list_sensors():
    raw = """
0x01010001) ICM45631 Accelerometer    | Invensense      | ver: 1 | type: android.sensor.accelerometer(1) | perm: n/a
0x01010005) TMD3743 Ambient Light     | AMS             | ver: 1 | type: android.sensor.light(5) | perm: n/a
"""
    sensors = smart.list_sensors(raw)
    assert len(sensors) == 2
    assert sensors[0]["name"] == "ICM45631 Accelerometer"
    assert sensors[0]["vendor"] == "Invensense"
