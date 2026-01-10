"""
Home Memory - Debug / Inspection UI

A developer console for testing and inspecting the Home Memory system.
This UI communicates ONLY via HTTP to the FastAPI backend.

NO direct database access.
NO imports from resolver/operations.
NO business logic.

Run with: streamlit run streamlit/debug_ui.py
Assumes FastAPI runs at: http://localhost:8000
"""

import json
from typing import List, Optional, Tuple

import requests
import streamlit as st

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

API_BASE_URL = "http://localhost:8000"

st.set_page_config(
    page_title="Home Memory - Debug Console",
    page_icon="🏠",
    layout="wide",
)

# Session state for password (NOT stored permanently)
if "pending_auth_request" not in st.session_state:
    st.session_state.pending_auth_request = None


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def parse_path(path_str: str) -> List[str]:
    """
    Parse comma-separated path string into list.
    
    "master bedroom, bed, left drawer" -> ["master bedroom", "bed", "left drawer"]
    """
    if not path_str or not path_str.strip():
        return []
    return [p.strip() for p in path_str.split(",") if p.strip()]


def api_request(
    method: str,
    endpoint: str,
    json_data: Optional[dict] = None,
    params: Optional[dict] = None,
    auth_password: Optional[str] = None,
) -> Tuple[int, dict]:
    """
    Make an API request and return (status_code, response_json).
    
    Args:
        method: HTTP method (GET, POST)
        endpoint: API endpoint
        json_data: JSON body for POST requests
        params: Query parameters for GET requests
        auth_password: Optional password for X-HomeMemory-Auth header
    """
    url = f"{API_BASE_URL}{endpoint}"
    headers = {}
    
    if auth_password:
        headers["X-HomeMemory-Auth"] = auth_password
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, params=params, headers=headers, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, json=json_data, headers=headers, timeout=10)
        else:
            return 500, {"error": f"Unsupported method: {method}"}
        
        # Handle empty responses (204 No Content)
        if response.status_code == 204:
            return 204, {"message": "Success (no content)"}
        
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, {"raw": response.text}
    
    except requests.exceptions.ConnectionError:
        return 0, {"error": "Connection failed. Is FastAPI running at http://localhost:8000?"}
    except requests.exceptions.Timeout:
        return 0, {"error": "Request timed out"}
    except Exception as e:
        return 0, {"error": str(e)}


def display_response(
    status_code: int,
    data: dict,
    request_info: Optional[dict] = None,
):
    """
    Display API response with appropriate styling.
    
    Args:
        status_code: HTTP status code
        data: Response data
        request_info: Optional dict with {method, endpoint, json_data, params}
                     for retry with auth (stored in session state for later)
    """
    if status_code == 0:
        st.error(f"❌ Connection Error")
        st.json(data)
    elif 200 <= status_code < 300:
        st.success(f"✅ Success (HTTP {status_code})")
        st.json(data)
        # Clear any pending auth request on success
        if "pending_auth_request" in st.session_state:
            st.session_state.pending_auth_request = None
    elif status_code == 404:
        st.warning(f"⚠️ Not Found (HTTP 404)")
        display_error_details(data)
    elif status_code == 409:
        st.warning(f"⚠️ Conflict (HTTP 409)")
        display_error_details(data)
    elif status_code == 400:
        st.error(f"❌ Bad Request (HTTP 400)")
        display_error_details(data)
    elif status_code == 403:
        st.error(f"❌ Forbidden (HTTP 403)")
        display_error_details(data)
    elif status_code == 423:
        st.error(f"🔒 Entity is Locked (HTTP 423)")
        display_error_details(data)
        
        # Store request info for retry (handled outside form)
        if request_info:
            st.session_state.pending_auth_request = request_info
            st.info("⬇️ Scroll down to authenticate and retry this request.")
    else:
        st.error(f"❌ Error (HTTP {status_code})")
        display_error_details(data)


def display_error_details(data: dict):
    """
    Display structured error details.
    """
    detail = data.get("detail", data)
    
    if isinstance(detail, dict):
        if "error_type" in detail:
            st.markdown(f"**Error Type:** `{detail['error_type']}`")
        if "message" in detail:
            st.markdown(f"**Message:** {detail['message']}")
        if "context" in detail and detail["context"]:
            st.markdown("**Context:**")
            st.json(detail["context"])
    else:
        st.json(data)


def fetch_tree_recursive(path_parts: List[str], depth: int = 0) -> List[dict]:
    """
    Recursively fetch container hierarchy.
    Returns list of {name, display_name, path, depth, children, items}.
    """
    status, data = api_request("POST", "/containers/children", {"path": path_parts})
    
    if status != 200:
        return []
    
    containers = data.get("containers", [])
    result = []
    
    for container in containers:
        # Parse the container name from its path
        name = container.get("name", "")
        display_name = container.get("display_name", name)
        child_path = path_parts + [name]
        
        # Recursively get children
        children = fetch_tree_recursive(child_path, depth + 1)
        
        result.append({
            "name": name,
            "display_name": display_name,
            "path": container.get("path", ""),
            "depth": depth,
            "container_type": container.get("container_type"),
            "is_locked": container.get("is_locked", False),
            "security_level": container.get("security_level", 0),
            "children": children,
        })
    
    return result


def render_tree(nodes: List[dict], indent: int = 0):
    """
    Render tree structure with indentation.
    Uses display_name for human-friendly output.
    """
    for node in nodes:
        prefix = "  " * indent
        lock_icon = "🔒" if node["is_locked"] else ""
        security = f"[L{node['security_level']}]" if node["security_level"] > 0 else ""
        type_hint = f"({node['container_type']})" if node["container_type"] else ""
        display = node.get("display_name", node["name"])
        
        st.text(f"{prefix}📁 {display} {type_hint} {lock_icon} {security}")
        st.caption(f"{prefix}   Path: {node['path']}")
        
        if node["children"]:
            render_tree(node["children"], indent + 1)


# -----------------------------------------------------------------------------
# Main UI
# -----------------------------------------------------------------------------

st.title("🏠 Home Memory - Debug Console")
st.caption("Developer/debug interface for testing the Home Memory API")

# Check API health
health_status, health_data = api_request("GET", "/health")
if health_status == 200:
    st.sidebar.success("✅ API Connected")
else:
    st.sidebar.error("❌ API Disconnected")
    st.sidebar.caption("Start FastAPI with: uvicorn app.main:app --reload")

st.sidebar.markdown("---")
st.sidebar.markdown("**API Base URL:**")
st.sidebar.code(API_BASE_URL)


# -----------------------------------------------------------------------------
# Tabs
# -----------------------------------------------------------------------------

tabs = st.tabs([
    "1. Init Root",
    "2. Create Container",
    "3. Create Item",
    "4. Move Container",
    "5. Move Item",
    "6. Rename Container",
    "7. Rename Item",
    "8. Lock/Unlock",
    "9. Resolve Path",
    "10. Find Item",
    "11. View Children",
    "12. Tree View",
    "13. Upload Image",
    "14. View Images",
])


# -----------------------------------------------------------------------------
# Tab 1: Initialize Root
# -----------------------------------------------------------------------------

with tabs[0]:
    st.header("Initialize Root Container")
    st.markdown("Creates the root `/home` container if it doesn't exist.")
    
    if st.button("Initialize Root", key="init_root"):
        status, data = api_request("POST", "/containers/init")
        display_response(status, data)


# -----------------------------------------------------------------------------
# Tab 2: Create Container
# -----------------------------------------------------------------------------

with tabs[1]:
    st.header("Create Container")
    st.markdown("Add a new container as a child of an existing container.")
    st.caption("If parent is locked, you'll be prompted for authentication.")
    
    with st.form("create_container_form"):
        parent_path = st.text_input(
            "Parent Path (comma-separated)",
            placeholder="master bedroom, closet",
            help="Leave empty for root (/home)",
        )
        name = st.text_input("Container Name", placeholder="left drawer")
        container_type = st.text_input("Type (optional)", placeholder="drawer")
        description = st.text_area("Description (optional)")
        
        submitted = st.form_submit_button("Create Container")
        
        if submitted:
            if not name:
                st.error("Container name is required")
            else:
                payload = {
                    "parent_path": parse_path(parent_path),
                    "name": name,
                }
                if container_type:
                    payload["container_type"] = container_type
                if description:
                    payload["description"] = description
                
                status, data = api_request("POST", "/containers", payload)
                request_info = {"method": "POST", "endpoint": "/containers", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 3: Create Item
# -----------------------------------------------------------------------------

with tabs[2]:
    st.header("Create Item")
    st.markdown("Add a new item to a container.")
    st.caption("If container is locked, you'll be prompted for authentication.")
    
    with st.form("create_item_form"):
        container_path = st.text_input(
            "Container Path (comma-separated)",
            placeholder="master bedroom, closet, left drawer",
        )
        name = st.text_input("Item Name", placeholder="passport")
        description = st.text_area("Description (optional)")
        quantity = st.number_input("Quantity", min_value=1, value=1)
        item_type = st.text_input("Type (optional)", placeholder="document")
        
        submitted = st.form_submit_button("Create Item")
        
        if submitted:
            if not name:
                st.error("Item name is required")
            else:
                payload = {
                    "container_path": parse_path(container_path),
                    "name": name,
                    "quantity": quantity,
                }
                if description:
                    payload["description"] = description
                if item_type:
                    payload["item_type"] = item_type
                
                status, data = api_request("POST", "/items", payload)
                request_info = {"method": "POST", "endpoint": "/items", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 4: Move Container
# -----------------------------------------------------------------------------

with tabs[3]:
    st.header("Move Container")
    st.markdown("Move a container (and all its contents) to a new parent.")
    st.caption("If source or destination is locked, you'll be prompted for authentication.")
    
    with st.form("move_container_form"):
        source_path = st.text_input(
            "Source Path (comma-separated)",
            placeholder="master bedroom, suitcase",
        )
        destination_path = st.text_input(
            "Destination Path (comma-separated)",
            placeholder="closet",
        )
        
        submitted = st.form_submit_button("Move Container")
        
        if submitted:
            if not source_path:
                st.error("Source path is required")
            else:
                payload = {
                    "source_path": parse_path(source_path),
                    "destination_path": parse_path(destination_path),
                }
                
                status, data = api_request("POST", "/containers/move", payload)
                request_info = {"method": "POST", "endpoint": "/containers/move", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 5: Move Item
# -----------------------------------------------------------------------------

with tabs[4]:
    st.header("Move Item")
    st.markdown("Move an item to a different container.")
    st.caption("If item or destination is locked, you'll be prompted for authentication.")
    
    with st.form("move_item_form"):
        item_name = st.text_input("Item Name", placeholder="passport")
        destination_path = st.text_input(
            "Destination Path (comma-separated)",
            placeholder="closet, safe",
        )
        
        submitted = st.form_submit_button("Move Item")
        
        if submitted:
            if not item_name:
                st.error("Item name is required")
            else:
                payload = {
                    "item_name": item_name,
                    "destination_path": parse_path(destination_path),
                }
                
                status, data = api_request("POST", "/items/move", payload)
                request_info = {"method": "POST", "endpoint": "/items/move", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 6: Rename Container
# -----------------------------------------------------------------------------

with tabs[5]:
    st.header("Rename Container")
    st.markdown("Rename a container (updates all descendant paths).")
    st.caption("If container is locked, you'll be prompted for authentication.")
    
    with st.form("rename_container_form"):
        path = st.text_input(
            "Container Path (comma-separated)",
            placeholder="master bedroom, left drawer",
        )
        new_name = st.text_input("New Name", placeholder="right drawer")
        
        submitted = st.form_submit_button("Rename Container")
        
        if submitted:
            if not path or not new_name:
                st.error("Both path and new name are required")
            else:
                payload = {
                    "path": parse_path(path),
                    "new_name": new_name,
                }
                
                status, data = api_request("POST", "/containers/rename", payload)
                request_info = {"method": "POST", "endpoint": "/containers/rename", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 7: Rename Item
# -----------------------------------------------------------------------------

with tabs[6]:
    st.header("Rename Item")
    st.markdown("Rename an item.")
    st.caption("If item is locked, you'll be prompted for authentication.")
    
    with st.form("rename_item_form"):
        item_name = st.text_input("Current Item Name", placeholder="passport")
        new_name = st.text_input("New Name", placeholder="old passport")
        
        submitted = st.form_submit_button("Rename Item")
        
        if submitted:
            if not item_name or not new_name:
                st.error("Both current name and new name are required")
            else:
                payload = {
                    "item_name": item_name,
                    "new_name": new_name,
                }
                
                status, data = api_request("POST", "/items/rename", payload)
                request_info = {"method": "POST", "endpoint": "/items/rename", "json_data": payload}
                display_response(status, data, request_info if status == 423 else None)


# -----------------------------------------------------------------------------
# Tab 8: Lock/Unlock Entity
# -----------------------------------------------------------------------------

with tabs[7]:
    st.header("Lock / Unlock Entity")
    st.markdown("Lock or unlock a container or item with security level.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Lock Container")
        with st.form("lock_container_form"):
            path = st.text_input(
                "Container Path",
                placeholder="master bedroom, safe",
                key="lock_container_path",
            )
            security_level = st.selectbox(
                "Security Level",
                options=[0, 1, 2],
                format_func=lambda x: {
                    0: "0 - Open (voice reveals)",
                    1: "1 - Restricted (voice confirms only)",
                    2: "2 - Secret (voice hides)",
                }[x],
                index=1,
                key="lock_container_level",
            )
            
            submitted = st.form_submit_button("Lock Container")
            
            if submitted:
                if not path:
                    st.error("Container path is required")
                else:
                    payload = {
                        "path": parse_path(path),
                        "security_level": security_level,
                    }
                    status, data = api_request("POST", "/containers/lock", payload)
                    display_response(status, data)
    
    with col2:
        st.subheader("Lock Item")
        with st.form("lock_item_form"):
            name = st.text_input(
                "Item Name",
                placeholder="gold",
                key="lock_item_name",
            )
            security_level = st.selectbox(
                "Security Level",
                options=[0, 1, 2],
                format_func=lambda x: {
                    0: "0 - Open (voice reveals)",
                    1: "1 - Restricted (voice confirms only)",
                    2: "2 - Secret (voice hides)",
                }[x],
                index=1,
                key="lock_item_level",
            )
            
            submitted = st.form_submit_button("Lock Item")
            
            if submitted:
                if not name:
                    st.error("Item name is required")
                else:
                    payload = {
                        "name": name,
                        "security_level": security_level,
                    }
                    status, data = api_request("POST", "/items/lock", payload)
                    display_response(status, data)


# -----------------------------------------------------------------------------
# Tab 9: Resolve Container Path
# -----------------------------------------------------------------------------

with tabs[8]:
    st.header("Resolve Container Path")
    st.markdown("Look up a container by its path.")
    
    with st.form("resolve_container_form"):
        path = st.text_input(
            "Path (comma-separated)",
            placeholder="master bedroom, closet, left drawer",
            help="Leave empty to resolve root (/home)",
        )
        
        submitted = st.form_submit_button("Resolve Path")
        
        if submitted:
            payload = {"path": parse_path(path)}
            status, data = api_request("POST", "/containers/resolve", payload)
            display_response(status, data)


# -----------------------------------------------------------------------------
# Tab 10: Find Item by Name
# -----------------------------------------------------------------------------

with tabs[9]:
    st.header("Find Item by Name")
    st.markdown("Search for an item globally by name.")
    
    with st.form("find_item_form"):
        name = st.text_input("Item Name", placeholder="passport")
        
        submitted = st.form_submit_button("Find Item")
        
        if submitted:
            if not name:
                st.error("Item name is required")
            else:
                status, data = api_request("GET", "/items/find", params={"name": name})
                display_response(status, data)
                
                # Also try to resolve (shows if ambiguous)
                st.markdown("---")
                st.markdown("**Resolve (single item):**")
                status2, data2 = api_request("GET", "/items/resolve", params={"name": name})
                display_response(status2, data2)


# -----------------------------------------------------------------------------
# Tab 11: View Children of Container
# -----------------------------------------------------------------------------

with tabs[10]:
    st.header("View Children of Container")
    st.markdown("List all direct child containers of a given container.")
    
    with st.form("view_children_form"):
        path = st.text_input(
            "Container Path (comma-separated)",
            placeholder="master bedroom",
            help="Leave empty for root (/home)",
        )
        
        submitted = st.form_submit_button("View Children")
        
        if submitted:
            payload = {"path": parse_path(path)}
            status, data = api_request("POST", "/containers/children", payload)
            display_response(status, data)


# -----------------------------------------------------------------------------
# Tab 12: Tree View
# -----------------------------------------------------------------------------

with tabs[11]:
    st.header("Tree View (Read-Only)")
    st.markdown("Visualize the full container hierarchy.")
    
    if st.button("Load Tree", key="load_tree"):
        with st.spinner("Loading hierarchy..."):
            # First resolve root
            status, root_data = api_request("POST", "/containers/resolve", {"path": []})
            
            if status != 200:
                display_response(status, root_data)
            else:
                # Display root
                st.markdown("### Container Hierarchy")
                st.text(f"📁 home (root)")
                st.caption(f"   Path: /home")
                
                # Fetch children recursively
                tree = fetch_tree_recursive([])
                
                if tree:
                    render_tree(tree, indent=1)
                else:
                    st.info("No child containers found. Create some containers first!")
                
                st.markdown("---")
                st.caption("🔒 = Locked | [L1]/[L2] = Security Level")


# -----------------------------------------------------------------------------
# Tab 13: Upload Image
# -----------------------------------------------------------------------------

with tabs[12]:
    st.header("Upload Image")
    st.markdown("Attach an image to a container or item.")
    st.caption("If entity is locked, you'll be prompted for authentication.")
    
    with st.form("upload_image_form"):
        entity_type = st.selectbox(
            "Entity Type",
            options=["container", "item"],
            key="upload_entity_type",
        )
        
        # User-friendly input: path for containers, name for items
        entity_identifier = st.text_input(
            "Container Path or Item Name",
            placeholder="e.g., 'master bedroom, closet' for container OR 'passport' for item",
            help="For containers: comma-separated path. For items: just the item name.",
        )
        
        uploaded_file = st.file_uploader(
            "Choose an image",
            type=["jpg", "jpeg", "png", "gif", "webp"],
            key="image_upload",
        )
        
        description = st.text_input("Description (optional)", placeholder="Front view of the drawer")
        is_primary = st.checkbox("Set as primary image", value=False)
        
        submitted = st.form_submit_button("Upload Image")
        
        if submitted:
            if not entity_identifier:
                st.error("Container path or item name is required")
            elif not uploaded_file:
                st.error("Please select an image file")
            else:
                # First, resolve the identifier to get the UUID
                entity_id = None
                
                if entity_type == "container":
                    # Resolve container path to get UUID
                    path_parts = parse_path(entity_identifier)
                    resolve_status, resolve_data = api_request(
                        "POST", "/containers/resolve", {"path": path_parts}
                    )
                    if resolve_status == 200:
                        entity_id = resolve_data.get("id")
                        st.info(f"Resolved container: {resolve_data.get('display_name', resolve_data.get('name'))} (ID: {entity_id[:8]}...)")
                    else:
                        st.error("❌ Could not resolve container path")
                        display_error_details(resolve_data)
                else:
                    # Find item by name to get UUID
                    resolve_status, resolve_data = api_request(
                        "GET", "/items/resolve", params={"name": entity_identifier.strip()}
                    )
                    if resolve_status == 200:
                        entity_id = resolve_data.get("id")
                        st.info(f"Resolved item: {resolve_data.get('display_name', resolve_data.get('name'))} (ID: {entity_id[:8]}...)")
                    else:
                        st.error("❌ Could not resolve item")
                        display_error_details(resolve_data)
                
                if entity_id:
                    # Now upload the image with the resolved UUID
                    try:
                        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                        form_data = {
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "is_primary": str(is_primary).lower(),
                        }
                        if description:
                            form_data["description"] = description
                        
                        import requests
                        response = requests.post(
                            f"{API_BASE_URL}/images/upload",
                            files=files,
                            data=form_data,
                            timeout=30,
                        )
                        
                        if response.status_code == 201:
                            st.success("✅ Image uploaded successfully!")
                            st.json(response.json())
                        elif response.status_code == 423:
                            st.error("🔒 Entity is Locked (HTTP 423)")
                            try:
                                display_error_details(response.json())
                            except:
                                st.text(response.text)
                            st.warning("Unlock the entity or use auth to upload images.")
                        else:
                            st.error(f"❌ Error (HTTP {response.status_code})")
                            try:
                                display_error_details(response.json())
                            except:
                                st.text(response.text)
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")


# -----------------------------------------------------------------------------
# Tab 14: View Images
# -----------------------------------------------------------------------------

with tabs[13]:
    st.header("View Images")
    st.markdown("View images attached to a container or item.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        view_entity_type = st.selectbox(
            "Entity Type",
            options=["container", "item"],
            key="view_entity_type",
        )
    
    with col2:
        view_entity_identifier = st.text_input(
            "Container Path or Item Name",
            placeholder="e.g., 'master bedroom, closet' or 'passport'",
            key="view_entity_identifier",
        )
    
    if st.button("Load Images", key="load_images"):
        if not view_entity_identifier:
            st.error("Container path or item name is required")
        else:
            # Resolve identifier to UUID
            view_entity_id = None
            
            if view_entity_type == "container":
                path_parts = parse_path(view_entity_identifier)
                resolve_status, resolve_data = api_request(
                    "POST", "/containers/resolve", {"path": path_parts}
                )
                if resolve_status == 200:
                    view_entity_id = resolve_data.get("id")
                    st.info(f"📁 {resolve_data.get('display_name', resolve_data.get('name'))} ({resolve_data.get('path')})")
                else:
                    st.error("❌ Could not resolve container path")
                    display_error_details(resolve_data)
            else:
                resolve_status, resolve_data = api_request(
                    "GET", "/items/resolve", params={"name": view_entity_identifier.strip()}
                )
                if resolve_status == 200:
                    view_entity_id = resolve_data.get("id")
                    st.info(f"📦 {resolve_data.get('display_name', resolve_data.get('name'))} ({resolve_data.get('path')})")
                else:
                    st.error("❌ Could not resolve item")
                    display_error_details(resolve_data)
            
            if not view_entity_id:
                st.stop()
            
            status, data = api_request(
                "GET",
                f"/images/entity/{view_entity_type}/{view_entity_id}",
            )
            
            if status == 200:
                images = data.get("images", [])
                primary_id = data.get("primary_id")
                
                if not images:
                    st.info("No images found for this entity.")
                else:
                    st.success(f"Found {len(images)} image(s)")
                    
                    # Display primary image large
                    primary_image = next((img for img in images if img.get("is_primary")), None)
                    
                    if primary_image:
                        st.markdown("### Primary Image")
                        st.image(
                            f"{API_BASE_URL}{primary_image['url']}",
                            caption=primary_image.get("description") or primary_image.get("filename"),
                            use_container_width=True,
                        )
                        st.caption(f"ID: `{primary_image['id']}` | Size: {primary_image.get('file_size', 'unknown')} bytes")
                    
                    # Display all images as thumbnails
                    st.markdown("### All Images")
                    
                    cols = st.columns(3)
                    for idx, img in enumerate(images):
                        with cols[idx % 3]:
                            is_primary = img.get("is_primary", False)
                            caption = f"{'⭐ ' if is_primary else ''}{img.get('filename', 'Unknown')}"
                            
                            st.image(
                                f"{API_BASE_URL}{img['url']}",
                                caption=caption,
                                use_container_width=True,
                            )
                            st.caption(f"ID: `{img['id']}`")
                            
                            if img.get("description"):
                                st.caption(img["description"])
                            
                            # Set as primary button
                            if not is_primary:
                                if st.button(f"Set Primary", key=f"primary_{img['id']}"):
                                    set_status, set_data = api_request(
                                        "POST",
                                        "/images/primary",
                                        {
                                            "entity_type": view_entity_type,
                                            "entity_id": view_entity_id,
                                            "image_id": img["id"],
                                        },
                                    )
                                    if set_status == 200:
                                        st.success("Set as primary!")
                                        st.rerun()
                                    else:
                                        display_error_details(set_data)
                            
                            # Delete button
                            if st.button(f"🗑️ Delete", key=f"delete_{img['id']}"):
                                del_status, del_data = api_request(
                                    "POST",
                                    "/images/delete",
                                    {"image_id": img["id"]},
                                )
                                if del_status == 204:
                                    st.success("Image deleted!")
                                    st.rerun()
                                else:
                                    display_error_details(del_data)
            else:
                display_response(status, data)


# -----------------------------------------------------------------------------
# Auth Retry Section (Outside all forms)
# -----------------------------------------------------------------------------

if st.session_state.pending_auth_request is not None:
    st.markdown("---")
    st.markdown("## 🔐 Authenticate to Access Locked Entity")
    st.markdown("The previous operation was blocked because the entity is locked. "
                "Enter password to retry with authentication.")
    st.markdown("*This does NOT unlock the entity - it provides temporary access for this request only.*")
    
    request_info = st.session_state.pending_auth_request
    
    st.markdown(f"**Pending request:** `{request_info.get('method', 'POST')} {request_info.get('endpoint', '')}`")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        auth_password = st.text_input(
            "Password",
            type="password",
            key="auth_retry_password",
            help="Enter 'homememory' for demo purposes",
        )
    
    with col2:
        st.write("")  # Spacing
        st.write("")  # Spacing
        retry_clicked = st.button("🔓 Retry with Auth", key="auth_retry_button")
    
    cancel_clicked = st.button("❌ Cancel", key="auth_cancel_button")
    
    if retry_clicked:
        if auth_password:
            status, data = api_request(
                method=request_info.get("method", "POST"),
                endpoint=request_info.get("endpoint", ""),
                json_data=request_info.get("json_data"),
                params=request_info.get("params"),
                auth_password=auth_password,
            )
            
            # Clear pending request since we've now retried
            st.session_state.pending_auth_request = None
            
            # Display the result
            st.markdown("### Retry Result")
            if status == 423:
                st.error("❌ Authentication failed or insufficient permissions")
                display_error_details(data)
            elif 200 <= status < 300:
                st.success(f"✅ Success (HTTP {status})")
                st.json(data)
            else:
                st.warning(f"⚠️ Request completed with status {status}")
                display_error_details(data)
        else:
            st.warning("Please enter a password")
    
    if cancel_clicked:
        st.session_state.pending_auth_request = None
        st.rerun()

