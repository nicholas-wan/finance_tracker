#Python==3.8. For some reason, 3.7 and 3.9 does not work.
# 8 Feb 2024

# Requirements
# pip install camelot-py
# pip install opencv-python

import camelot
import pandas as pd 
import os 

# Input is the CC statement downloaded from UOB
pdf_path = "UOB_CC_2024_01.pdf"

for f in os.listdir():
    if f[0]!='.':
        if 'UOB_CC' in f and '.pdf' in f:
            pdf_path = f
            print(f)

tables = camelot.read_pdf(pdf_path, flavor='stream', pages='all')
table_list = [table.df for table in tables]

#The parsing for the first table is a bit different, need to parse separately.
first_table = table_list[0].iloc[2:]
first_table.columns = ['Post', 'Trans', 'Description of Transaction', 'Transaction Amount']

# Strip the first 3 rows as the information is not needed
table_list = [table.iloc[3:] for table in table_list[1:]]
table_list.insert(0, first_table)

def make_new_header(df):
    # Convert the first row of the pandas dataframe to the header
    new_header = df.iloc[0] #grab the first row for the header
    df = df[1:] #take the data less the header row
    df.columns = new_header
    return df 

# Conver the 4th row to the table header, ['Post', 'Trans', 'Description of Transaction', 'Transaction Amount']
table_list = [make_new_header(table) for table in table_list]

new_table_list = []

# Only take the tables with the column Post, as there are some irrelevant tables
for table in table_list :
    if 'Post' in table.columns:
        new_table_list.append(table)

# Camelot reads some tables twice. Drop Duplicates
res = pd.concat(new_table_list).drop_duplicates().reset_index(drop=True) 
res = res[~res['Description of Transaction'].str.contains('Ref')].reset_index(drop=True)

# Sum the transaction amount
total_sum = list(res['Transaction Amount'])
sub_total, total_bal = total_sum[len(total_sum)-2:]
prev_bal = total_sum[1].replace(',','')

# Sum tohe total credit 
total_credit = [x for x in total_sum if 'CR' in x]
salary = total_credit[0].replace(',','')
remaining_credit = total_credit[1:]
total_sum = total_sum[2+len(total_credit):-2]
total_sum = [x for x in total_sum if 'CR' not in x]
remaining_credit = round(sum(float(x.replace(',','').replace(' CR','')) for x in remaining_credit if x not in ['SGD','']),2)
total_sum = round(sum(float(x.replace(',','')) for x in total_sum if x not in ['SGD','']),2)

# Print out all main stats for comparison, also to check if Calculated Sum == Sub Total
res_data = {'Previous Balance': [prev_bal], 'Salary':salary, 'Credit':[remaining_credit], 'Calculated Sum': [round(total_sum-remaining_credit,2)], 'Sub Total': [sub_total], 'Total Balance': [total_bal]}
res_data = pd.DataFrame.from_dict(res_data)
print(res_data)

# Save to CSV
res.to_csv(pdf_path.replace('.pdf','.csv'), index=False)