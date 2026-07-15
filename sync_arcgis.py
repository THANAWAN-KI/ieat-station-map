import json
import os
import sys
from datetime import datetime
from typing import Any

import requests


PORTAL_URL = "https://ieat.maps.arcgis.com"

SOURCE_URL = (
    "https://emonitor.ieat.go.th/"
    "call_feed/geog/GeoData/station_all.json"
)

TIMEOUT = 90


def post_json(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """ส่ง HTTP POST และคืนผลลัพธ์ JSON."""
    response = requests.post(url, data=data, timeout=TIMEOUT)
    response.raise_for_status()

    result = response.json()

    if "error" in result:
        error = result["error"]
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown ArcGIS error")
        details = "; ".join(error.get("details", []))

        raise RuntimeError(
            f"ArcGIS error {code}: {message}. {details}"
        )

    return result


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ส่ง HTTP GET และคืนผลลัพธ์ JSON."""
    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    result = response.json()

    if "error" in result:
        error = result["error"]
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown ArcGIS error")
        details = "; ".join(error.get("details", []))

        raise RuntimeError(
            f"ArcGIS error {code}: {message}. {details}"
        )

    return result


def generate_token(username: str, password: str) -> str:
    """สร้าง ArcGIS Online token จาก Username และ Password."""
    token_url = f"{PORTAL_URL}/sharing/rest/generateToken"

    result = post_json(
        token_url,
        {
            "f": "json",
            "username": username,
            "password": password,
            "client": "referer",
            "referer": PORTAL_URL,
            "expiration": 60,
        },
    )

    token = result.get("token")

    if not token:
        raise RuntimeError("ArcGIS Online did not return a token.")

    print("ArcGIS authentication successful.")
    return token


def get_feature_layer_url(item_id: str, token: str) -> str:
    """ค้นหา FeatureServer URL จาก ArcGIS Item ID."""
    item_url = (
        f"{PORTAL_URL}/sharing/rest/content/items/{item_id}"
    )

    item = get_json(
        item_url,
        {
            "f": "json",
            "token": token,
        },
    )

    print(f"Item title: {item.get('title', '-')}")
    print(f"Item type: {item.get('type', '-')}")

    service_url = item.get("url")

    if not service_url:
        raise RuntimeError(
            "Item นี้ไม่มี Feature Service URL "
            "กรุณาตรวจว่า ARCGIS_ITEM_ID เป็น Item ID "
            "ของ Hosted Feature Layer ไม่ใช่ Web Map หรือไฟล์ GeoJSON"
        )

    service_url = service_url.rstrip("/")

    if not service_url.lower().endswith("featureserver"):
        raise RuntimeError(
            f"Item URL ไม่ใช่ FeatureServer: {service_url}"
        )

    service_info = get_json(
        service_url,
        {
            "f": "json",
            "token": token,
        },
    )

    layers = service_info.get("layers", [])

    if not layers:
        raise RuntimeError(
            "Feature Service นี้ไม่มี Layer อยู่ภายใน"
        )

    layer_id = layers[0]["id"]
    layer_name = layers[0].get("name", str(layer_id))

    layer_url = f"{service_url}/{layer_id}"

    print(f"Layer name: {layer_name}")
    print(f"Layer URL: {layer_url}")

    return layer_url


def download_geojson() -> dict[str, Any]:
    """ดาวน์โหลด GeoJSON ต้นทางจาก IEAT."""
    print(f"Downloading IEAT data: {SOURCE_URL}")

    response = requests.get(
        SOURCE_URL,
        headers={
            "User-Agent": "IEAT-ArcGIS-Sync/1.0",
            "Accept": "application/json",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("type") != "FeatureCollection":
        raise RuntimeError(
            "ข้อมูลต้นทางไม่ใช่ GeoJSON FeatureCollection"
        )

    features = data.get("features")

    if not isinstance(features, list):
        raise RuntimeError("ไม่พบรายการ features ในข้อมูลต้นทาง")

    if len(features) == 0:
        raise RuntimeError(
            "ข้อมูลต้นทางมี features ว่าง "
            "สคริปต์จะไม่ลบข้อมูลใน ArcGIS"
        )

    print(f"Downloaded features: {len(features)}")
    return data


def parse_date_to_epoch(value: Any) -> int | None:
    """แปลงข้อความวันที่เป็น Unix epoch milliseconds."""
    if value in (None, "", "-"):
        return None

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]

    for date_format in formats:
        try:
            parsed = datetime.strptime(text, date_format)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue

    return None


def convert_value(
    value: Any,
    field: dict[str, Any],
) -> Any:
    """แปลงค่าให้ตรงกับชนิดข้อมูลของ ArcGIS Field."""
    if value in (None, ""):
        return None

    field_type = field.get("type")
    field_length = field.get("length")

    if field_type == "esriFieldTypeString":
        text = str(value)

        if field_length:
            text = text[: int(field_length)]

        return text

    if field_type in (
        "esriFieldTypeInteger",
        "esriFieldTypeSmallInteger",
        "esriFieldTypeOID",
    ):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    if field_type in (
        "esriFieldTypeDouble",
        "esriFieldTypeSingle",
    ):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if field_type == "esriFieldTypeDate":
        return parse_date_to_epoch(value)

    return value


def build_arcgis_features(
    geojson: dict[str, Any],
    layer_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """แปลง GeoJSON Features เป็น ArcGIS JSON Features."""
    excluded_types = {
        "esriFieldTypeOID",
        "esriFieldTypeGeometry",
        "esriFieldTypeGlobalID",
        "esriFieldTypeGUID",
    }

    excluded_names = {
        layer_info.get("objectIdField"),
        layer_info.get("globalIdField"),
    }

    editable_fields: dict[str, dict[str, Any]] = {}

    for field in layer_info.get("fields", []):
        field_name = field.get("name")
        field_type = field.get("type")

        if not field_name:
            continue

        if field_name in excluded_names:
            continue

        if field_type in excluded_types:
            continue

        if field.get("editable") is False:
            continue

        editable_fields[field_name.lower()] = field

    if not editable_fields:
        raise RuntimeError(
            "ไม่พบ Field ที่สามารถเขียนข้อมูลได้ใน Hosted Layer"
        )

    arcgis_features: list[dict[str, Any]] = []
    matched_field_names: set[str] = set()

    for index, feature in enumerate(
        geojson.get("features", [])
    ):
        geometry = feature.get("geometry") or {}

        if geometry.get("type") != "Point":
            print(
                f"Skipping feature {index}: geometry is not Point"
            )
            continue

        coordinates = geometry.get("coordinates")

        if (
            not isinstance(coordinates, list)
            or len(coordinates) < 2
        ):
            print(
                f"Skipping feature {index}: invalid coordinates"
            )
            continue

        longitude = coordinates[0]
        latitude = coordinates[1]

        try:
            longitude = float(longitude)
            latitude = float(latitude)
        except (TypeError, ValueError):
            print(
                f"Skipping feature {index}: "
                "coordinates are not numbers"
            )
            continue

        if not -180 <= longitude <= 180:
            print(
                f"Skipping feature {index}: invalid longitude"
            )
            continue

        if not -90 <= latitude <= 90:
            print(
                f"Skipping feature {index}: invalid latitude"
            )
            continue

        source_properties = feature.get("properties") or {}
        attributes: dict[str, Any] = {}

        # เปรียบเทียบชื่อ Field โดยไม่สนตัวพิมพ์ใหญ่-เล็ก
        source_lookup = {
            str(key).lower(): value
            for key, value in source_properties.items()
        }

        for lower_name, field in editable_fields.items():
            if lower_name not in source_lookup:
                continue

            field_name = field["name"]
            value = source_lookup[lower_name]

            attributes[field_name] = convert_value(
                value,
                field,
            )

            matched_field_names.add(field_name)

        arcgis_features.append(
            {
                "geometry": {
                    "x": longitude,
                    "y": latitude,
                    "spatialReference": {
                        "wkid": 4326
                    },
                },
                "attributes": attributes,
            }
        )

    if not arcgis_features:
        raise RuntimeError(
            "ไม่สามารถสร้าง Point จากข้อมูลต้นทางได้"
        )

    print(
        "Matched ArcGIS fields: "
        + ", ".join(sorted(matched_field_names))
    )

    if not matched_field_names:
        raise RuntimeError(
            "ชื่อ Field ใน Hosted Layer ไม่ตรงกับ "
            "properties ใน GeoJSON เลย"
        )

    print(
        f"Prepared ArcGIS features: {len(arcgis_features)}"
    )

    return arcgis_features


def get_existing_object_ids(
    layer_url: str,
    token: str,
) -> list[int]:
    """อ่าน Object ID เดิมทั้งหมดของ Hosted Layer."""
    query_url = f"{layer_url}/query"

    result = post_json(
        query_url,
        {
            "f": "json",
            "token": token,
            "where": "1=1",
            "returnIdsOnly": "true",
        },
    )

    object_ids = result.get("objectIds") or []

    print(f"Existing ArcGIS features: {len(object_ids)}")
    return object_ids


def apply_edits(
    layer_url: str,
    token: str,
    new_features: list[dict[str, Any]],
    old_object_ids: list[int],
) -> None:
    """
    เพิ่มข้อมูลใหม่และลบข้อมูลเดิมในคำขอเดียว
    พร้อม rollback หากส่วนใดส่วนหนึ่งล้มเหลว
    """
    apply_edits_url = f"{layer_url}/applyEdits"

    payload: dict[str, Any] = {
        "f": "json",
        "token": token,
        "adds": json.dumps(
            new_features,
            ensure_ascii=False,
        ),
        "rollbackOnFailure": "true",
        "useGlobalIds": "false",
    }

    if old_object_ids:
        payload["deletes"] = ",".join(
            str(object_id)
            for object_id in old_object_ids
        )

    print("Applying edits to ArcGIS Online...")

    result = post_json(apply_edits_url, payload)

    add_results = result.get("addResults", [])
    delete_results = result.get("deleteResults", [])

    failed_adds = [
        row for row in add_results
        if not row.get("success")
    ]

    failed_deletes = [
        row for row in delete_results
        if not row.get("success")
    ]

    if failed_adds or failed_deletes:
        raise RuntimeError(
            "applyEdits returned failures:\n"
            f"Add failures: {failed_adds}\n"
            f"Delete failures: {failed_deletes}"
        )

    if len(add_results) != len(new_features):
        raise RuntimeError(
            "จำนวน Feature ที่ ArcGIS ตอบกลับไม่ตรงกับ "
            "จำนวนที่ส่งเข้าไป"
        )

    print(f"Added features: {len(add_results)}")
    print(f"Deleted old features: {len(delete_results)}")


def verify_feature_count(
    layer_url: str,
    token: str,
    expected_count: int,
) -> None:
    """ตรวจจำนวน Feature หลังอัปเดต."""
    query_url = f"{layer_url}/query"

    result = post_json(
        query_url,
        {
            "f": "json",
            "token": token,
            "where": "1=1",
            "returnCountOnly": "true",
        },
    )

    actual_count = result.get("count")

    print(f"ArcGIS feature count after update: {actual_count}")

    if actual_count != expected_count:
        raise RuntimeError(
            f"ตรวจสอบไม่ผ่าน: คาดว่า {expected_count} จุด "
            f"แต่ ArcGIS มี {actual_count} จุด"
        )


def main() -> None:
    username = os.environ.get("ARCGIS_USERNAME")
    password = os.environ.get("ARCGIS_PASSWORD")
    item_id = os.environ.get("ARCGIS_ITEM_ID")

    missing = []

    if not username:
        missing.append("ARCGIS_USERNAME")

    if not password:
        missing.append("ARCGIS_PASSWORD")

    if not item_id:
        missing.append("ARCGIS_ITEM_ID")

    if missing:
        raise RuntimeError(
            "Missing GitHub Secrets: "
            + ", ".join(missing)
        )

    token = generate_token(username, password)

    layer_url = get_feature_layer_url(
        item_id,
        token,
    )

    layer_info = get_json(
        layer_url,
        {
            "f": "json",
            "token": token,
        },
    )

    capabilities = str(
        layer_info.get("capabilities", "")
    ).lower()

    print(
        "Layer capabilities: "
        + layer_info.get("capabilities", "-")
    )

    if "editing" not in capabilities:
        print(
            "Warning: Layer capabilities does not explicitly "
            "show Editing. The script will still test applyEdits."
        )

    geojson = download_geojson()

    new_features = build_arcgis_features(
        geojson,
        layer_info,
    )

    old_object_ids = get_existing_object_ids(
        layer_url,
        token,
    )

    apply_edits(
        layer_url,
        token,
        new_features,
        old_object_ids,
    )

    verify_feature_count(
        layer_url,
        token,
        len(new_features),
    )

    print("IEAT → ArcGIS Online synchronization completed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"SYNC FAILED: {error}", file=sys.stderr)
        sys.exit(1)
