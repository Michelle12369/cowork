import { render } from '@testing-library/react';
import { getFileIcon } from './fileIcon';

test('getFileIcon_xlsxFilename_rendersExcelIcon', () => {
  const { container } = render(<>{getFileIcon('sales.xlsx')}</>);
  expect(container.querySelector('[aria-label="file-excel"]')).not.toBeNull();
});

test('getFileIcon_csvFilename_rendersTextIcon', () => {
  const { container } = render(<>{getFileIcon('sales.csv')}</>);
  expect(container.querySelector('[aria-label="file-text"]')).not.toBeNull();
});

test('getFileIcon_uppercaseExtension_stillRendersExcelIcon', () => {
  const { container } = render(<>{getFileIcon('SALES.XLSX')}</>);
  expect(container.querySelector('[aria-label="file-excel"]')).not.toBeNull();
});

test('getFileIcon_filenameWithoutExtension_rendersTextIcon', () => {
  const { container } = render(<>{getFileIcon('README')}</>);
  expect(container.querySelector('[aria-label="file-text"]')).not.toBeNull();
});
