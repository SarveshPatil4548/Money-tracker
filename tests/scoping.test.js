import test from "node:test";
import assert from "node:assert";

function scopeByUser(items, userId) {
  return items.filter((i) => String(i.userId) === String(userId));
}

test("scoping: only own data returned", () => {
  const items = [
    { id: 1, userId: "u1", name: "a" },
    { id: 2, userId: "u2", name: "b" },
    { id: 3, userId: "u1", name: "c" },
  ];
  assert.equal(scopeByUser(items, "u1").length, 2);
  assert.equal(scopeByUser(items, "u2").length, 1);
  assert.equal(scopeByUser(items, "u3").length, 0);
});
