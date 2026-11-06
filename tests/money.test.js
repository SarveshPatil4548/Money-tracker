import test from "node:test";
import assert from "node:assert";

function toMinor(x) {
  return Math.round(Number(x) * 100);
}

function fromMinor(x) {
  return (Number(x) / 100).toFixed(2);
}

test("money: toMinor/fromMinor", () => {
  assert.equal(toMinor(1250.75), 125075);
  assert.equal(fromMinor(125075), "1250.75");
  assert.equal(toMinor(10), 1000);
});

test("money: rounding", () => {
  assert.equal(toMinor(10.555), 1056);
});
