import assert from 'node:assert/strict';
import {markdownOutline,csvText,localAsset} from '../src/rich-content-utils.ts';
assert.deepEqual(markdownOutline('# 标题\n```md\n# 假标题\n```\n## 标题\n## 表 **数据**'),[
  {level:1,text:'标题',slug:'标题'},{level:2,text:'标题',slug:'标题-2'},{level:2,text:'表 数据',slug:'表-数据'},
]);
assert.equal(csvText([['名称','a,b'],['"value"','line\n2']]),'名称,"a,b"\r\n"""value""","line\n2"');
assert.equal(localAsset('javascript:alert(1)','abc'),'');
assert.equal(localAsset('/home/task/plot.png','a'.repeat(32)),'/api/research-chat/'+ 'a'.repeat(32) +'/artifact?path=%2Fhome%2Ftask%2Fplot.png');
assert.equal(localAsset('https://example.com/image.png','abc'),'https://example.com/image.png');
console.log('PASS: rich content outline, fenced-code exclusion, unique anchors, CSV quoting and local asset routing');
