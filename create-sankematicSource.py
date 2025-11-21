import pandas as pd
import argparse
import json
import os
from datetime import datetime
import sys

# ==========================================
# CONFIGURATION: OVERWRITE MAP (Hardcoded Defaults)
# ==========================================
# Use a standard Python dictionary for the default map.
HARDCODED_OVERWRITE_MAP = {
    "Azure Database for PostgreSQL": "PostgreSQL",
    "Azure Cognitive Search": "Azure Search",
}

def get_label(name, overwrite_map):
    """Retrieves the display label from the overwrite map."""
    return overwrite_map.get(name, name)

def load_overwrite_map(json_file_path):
    """Loads and merges the hardcoded map with an optional JSON file."""
    overwrite_map = HARDCODED_OVERWRITE_MAP.copy()
    
    if json_file_path:
        if os.path.exists(json_file_path):
            try:
                with open(json_file_path, 'r') as f:
                    # Load JSON. 
                    external_map = json.load(f)
                
                # Merge: external map overrides hardcoded map if keys overlap
                overwrite_map.update(external_map)
                print(f"Loaded overwrite map from '{json_file_path}'")
            except Exception as e:
                print(f"Warning: Failed to parse JSON file. Using defaults only. Error: {e}", file=sys.stderr)
        else:
            print(f"Warning: JSON file '{json_file_path}' not found. Using defaults.", file=sys.stderr)
            
    return overwrite_map

def generate_sankey_data(df, top_categories, overwrite_map):
    """
    Applies business logic and generates the SankeyMATIC flow string.
    """
    
    # 2. Clean and Pre-process Data
    
    # Clean 'Monthy Cost' and round to integer
    df['Monthy Cost'] = df['Monthy Cost'].astype(str).str.replace(',', '').astype(float).round(0).astype(int)

    # Combine Categories to "ETL Tools"
    df['MeterCategory'] = df['MeterCategory'].replace(['Azure Data Factory v2', 'Azure Synapse Analytics'], 'ETL Tools')
    
    # --- 3. Logic: Identify Categories to Keep ---
    
    # Calculate total cost per category for sorting
    grouped_by_cat = df.groupby('MeterCategory')['Monthy Cost'].sum()

    # 3a. Identify categories with Reservations (Mandatory)
    reservation_cats = df[df['PricingModel'] == 'Reservation']['MeterCategory'].unique().tolist()
    
    total_unique_cats = len(grouped_by_cat)
    categories_to_keep = []

    if total_unique_cats <= top_categories:
        categories_to_keep = grouped_by_cat.index.tolist()
    else:
        # "Others" takes 1 slot
        slots_for_specific = top_categories - 1
        mandatory = reservation_cats
        
        if len(mandatory) >= slots_for_specific:
            categories_to_keep = mandatory
        else:
            slots_remaining = slots_for_specific - len(mandatory)
            
            # Get candidates NOT in mandatory list, sort by cost descending
            candidates = grouped_by_cat.drop(labels=mandatory, errors='ignore').sort_values(ascending=False)
            
            # Fill remaining slots
            fillers = candidates.head(slots_remaining).index.tolist()
            categories_to_keep = list(set(mandatory) | set(fillers))

    # --- 4. Apply "Others" Grouping ---
    
    def apply_grouping(category):
        return category if category in categories_to_keep else 'Others'
    
    df['MeterCategory_Grouped'] = df['MeterCategory'].apply(apply_grouping)

    # --- 5. Calculate Sort Orders (Descending by Cost) ---
    
    category_sort_list = df.groupby('MeterCategory_Grouped')['Monthy Cost'].sum().sort_values(ascending=False)
    environment_sort_list = df.groupby('Environment')['Monthy Cost'].sum().sort_values(ascending=False)
    pricing_order = ['SavingsPlan', 'Reservation', 'OnDemand']

    # --- 6. Generate Sankey Output String ---
    
    sankey_lines = []
    
    # Flow 1: PricingModel -> MeterCategory_Grouped
    for model in pricing_order:
        model_df = df[df['PricingModel'] == model]
        model_cat_costs = model_df.groupby('MeterCategory_Grouped')['Monthy Cost'].sum()
        
        for cat_name in category_sort_list.index:
            if cat_name in model_cat_costs.index and model_cat_costs[cat_name] > 0:
                cost = model_cat_costs[cat_name]
                label = get_label(cat_name, overwrite_map)
                sankey_lines.append(f"{model} [{cost}] {label}")

    sankey_lines.append("") # Empty line between flows

    # Flow 2: MeterCategory_Grouped -> TotalMonthly
    for cat_name, cost in category_sort_list.items():
        label = get_label(cat_name, overwrite_map)
        sankey_lines.append(f"{label} [{cost}] TotalMonthly")
    
    sankey_lines.append("") # Empty line between flows

    # Flow 3: TotalMonthly -> Environment
    for env_name, cost in environment_sort_list.items():
        # Note: Environment labels are currently passed through get_label for consistency,
        # but the hardcoded map only contains MeterCategory names.
        label = get_label(env_name, overwrite_map)
        sankey_lines.append(f"TotalMonthly [{cost}] {label}")
        
    return "\n".join(sankey_lines)

def main():
    parser = argparse.ArgumentParser(
        description="Generates SankeyMATIC flow data from a cloud cost CSV.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument(
        '--csvFile',
        required=True,
        help="Path to the source cost data CSV file."
    )
    parser.add_argument(
        '--TopCategories',
        type=int,
        default=9,
        help="The TOTAL number of categories to display in the middle column (including 'Others').\nDefault is 9."
    )
    parser.add_argument(
        '--OverwriteJsonFile',
        type=str,
        default=None,
        help="Optional path to a JSON file for custom label remapping."
    )
    parser.add_argument(
        '--Formatted',
        action='store_true',
        help="If set, the output is injected into 'sankeymatic_template.txt' and saved."
    )
    parser.add_argument(
        '--Screen',
        action='store_true',
        help="If set, the generated Sankey flow data is printed to the console."
    )
    
    args = parser.parse_args()

    # --- 1. Load Data ---
    if not os.path.exists(args.csvFile):
        print(f"Error: Input file '{args.csvFile}' not found.", file=sys.stderr)
        return
        
    try:
        df = pd.read_csv(args.csvFile)
    except Exception as e:
        print(f"Error reading CSV file: {e}", file=sys.stderr)
        return

    # --- 2. Load Overwrite Map ---
    overwrite_map = load_overwrite_map(args.OverwriteJsonFile)

    # --- 3. Generate Sankey Output ---
    sankey_output = generate_sankey_data(df, args.TopCategories, overwrite_map)
    
    # --- 4. Handle Outputs ---
    
    # Output to Screen
    if args.Screen:
        print(sankey_output)
        
    # Output to File (Formatted)
    if args.Formatted:
        template_file = "sankeymatic_template.txt"
        marker = "// === Nodes and Flows ==="
        timestamp_placeholder = "%GENERATED DATETIME%"

        if os.path.exists(template_file):
            try:
                with open(template_file, 'r') as f:
                    template_content = f.read()
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                file_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                output_filename = f"sankeymatic_{file_timestamp}.txt"

                # Replace Timestamp
                template_content = template_content.replace(timestamp_placeholder, timestamp)

                # Insert into Nodes and Flows section
                if marker in template_content:
                    replacement = f"{marker}\n{sankey_output}"
                    final_content = template_content.replace(marker, replacement)
                    
                    with open(output_filename, 'w') as f:
                        f.write(final_content)
                    print(f"Successfully created formatted file: {output_filename}")
                else:
                    print(f"Warning: Template file found but '{marker}' section missing. File not saved.", file=sys.stderr)
            except Exception as e:
                print(f"Error processing template file: {e}", file=sys.stderr)
        else:
            print(f"Warning: Template file '{template_file}' not found. Skipping file generation.", file=sys.stderr)

if __name__ == "__main__":
    main()