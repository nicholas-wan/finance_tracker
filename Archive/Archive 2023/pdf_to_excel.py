
input_pdf = "UOB_CC_2023_06.pdf"
output_pdf =  "data_jul.csv"


# Import Module
import pdftables_api
  
# API KEY VERIFICATION
conversion = pdftables_api.Client('API KEY')
  
# PDf to Excel 
# (Hello.pdf, Hello)
conversion.xlsx("pdf_file_path", "output_file_path")