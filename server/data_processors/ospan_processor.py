import os
import pandas as pd

class OspanProcessor:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.trial_n = (4*3) + (6*3) # There are 75 trials in the ospan task, but only 30 trials in the online version.

    def create_index(self, data, trial_n):
        output = list(data.index)
        output.reverse()
        output = output[:trial_n]
        output.reverse()
        return output

    def select_item(self, data, trial_n):
        output = data.dropna(how='any')
        index = self.create_index(output, trial_n)
        return output.loc[index, :]

    def math_analysis(self, data):
        output = data[['MathResult']]
        output = self.select_item(output, self.trial_n)
        return output.mean().values[0]

    def letter_analysis(self, data):
        output = data[['LetterResult']]
        output = self.select_item(output, self.trial_n)
        return output.mean().values[0]

    def process_subject(self, file_path):
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return None

        data = pd.read_csv(file_path)
        id = data.loc[0, '指定代號']
        math_result = self.math_analysis(data)
        letter_result = self.letter_analysis(data)

        output = pd.DataFrame({
            'ID': [id],
            'MEMORY_OSPAN_BEH_MATH_ACCURACY': [math_result],
            'MEMORY_OSPAN_BEH_LETTER_ACCURACY': [letter_result]
        }) 

        return output
