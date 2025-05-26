from flask import Flask, request, send_file, render_template
import pandas as pd
from io import BytesIO
import unicodedata

app = Flask(__name__)

def normalize_name(name):
    if pd.isna(name):
        return ""
    name = str(name).strip().upper()
    name = unicodedata.normalize("NFKD", name).replace("\xa0", " ")
    return " ".join(name.split())  # Collapse multiple spaces into one

def normalize_columns(df):
    df.columns = df.columns.str.strip().str.upper()
    if 'NAME' in df.columns:
        df['NAME'] = df['NAME'].apply(normalize_name)
    return df

@app.route('/')
def home():
    return render_template('multi.html')

@app.route('/auto-allocation', methods=['POST'])
def auto_allocation():
    if not all(k in request.files for k in ['main_file', 'associate_file', 'pending_file']):
        return "Please upload all required files: main, associate, and pending.", 400

    main_df = normalize_columns(pd.read_excel(request.files['main_file']))
    associate_df = normalize_columns(pd.read_excel(request.files['associate_file']))
    pending_df = normalize_columns(pd.read_excel(request.files['pending_file']))

    # Calculate pending counts
    pending_counts = pending_df['NAME'].value_counts().reset_index()
    pending_counts.columns = ['NAME', 'PENDING_COUNT']

    # Normalize associate list
    associate_df['NAME'] = associate_df['NAME'].apply(normalize_name)

    merged = associate_df.merge(pending_counts, on='NAME', how='left').fillna(0)
    merged['PENDING_COUNT'] = merged['PENDING_COUNT'].astype(int)

    # Compute inverse weight for allocation
    merged['WEIGHT'] = 1 / (merged['PENDING_COUNT'] + 1)
    total_weight = merged['WEIGHT'].sum()
    merged['ALLOCATION_COUNT'] = ((merged['WEIGHT'] / total_weight) * len(main_df)).round().astype(int)

    # Adjust for rounding issues
    while merged['ALLOCATION_COUNT'].sum() > len(main_df):
        idx = merged['ALLOCATION_COUNT'].idxmax()
        merged.at[idx, 'ALLOCATION_COUNT'] -= 1
    while merged['ALLOCATION_COUNT'].sum() < len(main_df):
        idx = merged['ALLOCATION_COUNT'].idxmin()
        merged.at[idx, 'ALLOCATION_COUNT'] += 1

    # Distribute estimates
    result_rows = []
    estimate_index = 0
    for _, row in merged.iterrows():
        count = row['ALLOCATION_COUNT']
        name = row['NAME']
        assigned = main_df.iloc[estimate_index:estimate_index + count].copy()
        assigned['ASSOCIATE_NAME'] = name
        result_rows.append(assigned)
        estimate_index += count

    result_df = pd.concat(result_rows)

    # Prepare summary
    summary = merged[['NAME', 'PENDING_COUNT', 'ALLOCATION_COUNT']]
    summary.columns = ['ASSOCIATE_NAME', 'PENDING_COUNT', 'ALLOCATED']

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        result_df.to_excel(writer, sheet_name='Allocations', index=False)
        summary.to_excel(writer, sheet_name='Summary', index=False)

    output.seek(0)
    return send_file(output, download_name='allocation_result.xlsx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)