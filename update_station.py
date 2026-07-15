import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SOURCE_URL = (
    "https://emonitor.ieat.go.th/"
    "call_feed/geog/GeoData/station_all.json"
)

OUTPUT_FILE = Path("station.geojson")
METADATA_FILE = Path("update_metadata.json")


def download_json(url: str) -> dict:
    """ดาวน์โหลดและแปลงข้อมูลจาก URL เป็น Python dictionary."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 IEAT-GeoJSON-Updater/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw_data = response.read().decode(charset)

    return json.loads(raw_data)


def validate_geojson(data: dict) -> int:
    """ตรวจสอบโครงสร้างพื้นฐานของ GeoJSON และคืนจำนวนจุด."""
    if not isinstance(data, dict):
        raise ValueError("ข้อมูลหลักต้องเป็น JSON object")

    if data.get("type") != "FeatureCollection":
        raise ValueError(
            "ข้อมูลไม่ใช่ GeoJSON FeatureCollection"
        )

    features = data.get("features")

    if not isinstance(features, list):
        raise ValueError("ไม่พบรายการ features")

    valid_points = 0

    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            raise ValueError(
                f"Feature ลำดับที่ {index} ไม่ใช่ object"
            )

        geometry = feature.get("geometry")

        if not geometry:
            continue

        if geometry.get("type") != "Point":
            continue

        coordinates = geometry.get("coordinates")

        if (
            not isinstance(coordinates, list)
            or len(coordinates) < 2
        ):
            raise ValueError(
                f"Feature ลำดับที่ {index} มีพิกัดไม่ถูกต้อง"
            )

        longitude = coordinates[0]
        latitude = coordinates[1]

        if not isinstance(longitude, (int, float)):
            raise ValueError(
                f"Longitude ลำดับที่ {index} ไม่ใช่ตัวเลข"
            )

        if not isinstance(latitude, (int, float)):
            raise ValueError(
                f"Latitude ลำดับที่ {index} ไม่ใช่ตัวเลข"
            )

        if not -180 <= longitude <= 180:
            raise ValueError(
                f"Longitude ลำดับที่ {index} อยู่นอกช่วง"
            )

        if not -90 <= latitude <= 90:
            raise ValueError(
                f"Latitude ลำดับที่ {index} อยู่นอกช่วง"
            )

        valid_points += 1

    if valid_points == 0:
        raise ValueError("ไม่พบ Point ที่มีพิกัดถูกต้อง")

    return valid_points


def save_geojson(data: dict, output_file: Path) -> None:
    """บันทึก GeoJSON โดยรองรับตัวอักษรภาษาไทย."""
    output_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def save_metadata(feature_count: int) -> None:
    """บันทึกข้อมูลประกอบเกี่ยวกับการอัปเดต."""
    metadata = {
        "source_url": SOURCE_URL,
        "updated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "feature_count": feature_count,
        "output_file": str(OUTPUT_FILE),
    }

    METADATA_FILE.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    try:
        print(f"Downloading: {SOURCE_URL}")

        geojson_data = download_json(SOURCE_URL)
        feature_count = validate_geojson(geojson_data)

        save_geojson(geojson_data, OUTPUT_FILE)
        save_metadata(feature_count)

        print(
            f"Success: saved {feature_count} points "
            f"to {OUTPUT_FILE}"
        )

    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
